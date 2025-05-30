import datetime
import json
import logging
import random
import time
import uuid
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlmodel import Session, select
from sqlalchemy.exc import NoResultFound
from starlette.datastructures import UploadFile

from keep.api.core.config import config
from keep.api.core.db import count_alerts, get_provider_distribution, get_session
from keep.api.core.limiter import limiter
from keep.api.models.db.provider import Provider
from keep.api.models.provider import Provider as ProviderDTO
from keep.api.models.provider import ProviderAlertsCountResponseDTO
from keep.api.models.webhook import ProviderWebhookSettings
from keep.api.utils.tenant_utils import get_or_create_api_key
from keep.contextmanager.contextmanager import ContextManager
from keep.exceptions.provider_exception import ProviderException
from keep.identitymanager.authenticatedentity import AuthenticatedEntity
from keep.identitymanager.identitymanagerfactory import IdentityManagerFactory
from keep.providers.base.provider_exceptions import (
    GetAlertException,
    ProviderMethodException,
)
from keep.providers.providers_factory import (
    ProviderConfigurationException,
    ProvidersFactory,
)
from keep.providers.providers_service import ProvidersService
from keep.secretmanager.secretmanagerfactory import SecretManagerFactory

router = APIRouter()
logger = logging.getLogger(__name__)

READ_ONLY = config("KEEP_READ_ONLY", default="false") == "true"
PROVIDER_DISTRIBUTION_ENABLED = config(
    "KEEP_PROVIDER_DISTRIBUTION_ENABLED", cast=bool, default=True
)


def _is_localhost():
    # TODO - there are more "advanced" cases that we don't catch here
    #        e.g. IP's that are not public but not localhost
    #        the more robust way is to try access KEEP_API_URL from another tool (such as wtfismy.com but the opposite)
    #
    #        this is a temporary solution until we have a better one
    api_url = config("KEEP_API_URL")
    if "localhost" in api_url:
        return True

    if "127.0.0" in api_url:
        return True

    # default on localhost if no USE_NGROK
    if "0.0.0.0" in api_url:
        return True

    return False


@router.get("", description="Get all providers")
def get_providers(
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["read:providers"])
    ),
):
    tenant_id = authenticated_entity.tenant_id
    logger.info("Getting installed providers", extra={"tenant_id": tenant_id})
    providers = ProvidersService.get_all_providers()
    installed_providers = ProvidersService.get_installed_providers(tenant_id)
    linked_providers = ProvidersService.get_linked_providers(tenant_id)
    if PROVIDER_DISTRIBUTION_ENABLED:
        # generate distribution only if not in read only mode
        if READ_ONLY:
            for provider in linked_providers + installed_providers:
                if "alert" not in provider.tags:
                    continue
                provider.alertsDistribution = [
                    {"hour": i, "number": random.randint(0, 100)} for i in range(0, 24)
                ]
                provider.last_alert_received = datetime.datetime.now().isoformat()
        else:
            providers_distribution = get_provider_distribution(tenant_id)
            for provider in linked_providers + installed_providers:
                provider.alertsDistribution = providers_distribution.get(
                    f"{provider.id}_{provider.type}", {}
                ).get("alert_last_24_hours", [])
                last_alert_received = providers_distribution.get(
                    f"{provider.id}_{provider.type}", {}
                ).get("last_alert_received", None)
                if last_alert_received and not provider.last_alert_received:
                    provider.last_alert_received = last_alert_received.replace(
                        tzinfo=datetime.timezone.utc
                    ).isoformat()

    is_localhost = _is_localhost()

    return {
        "providers": providers,
        "installed_providers": installed_providers,
        "linked_providers": linked_providers,
        "is_localhost": is_localhost,
    }


@router.get("/{provider_id}/logs", description="Get provider logs")
def get_provider_logs(
    provider_id: str,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["read:providers"])
    ),
):
    tenant_id = authenticated_entity.tenant_id
    logger.info(
        "Getting provider logs",
        extra={"tenant_id": tenant_id, "provider_id": provider_id},
    )

    try:
        logs = ProvidersService.get_provider_logs(tenant_id, provider_id)
        return JSONResponse(content=jsonable_encoder(logs), status_code=200)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(
            f"Error getting provider logs: {str(e)}",
            extra={"tenant_id": tenant_id, "provider_id": provider_id},
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/export",
    description="Export all installed providers",
    response_model=list[ProviderDTO],
)
@limiter.exempt
def get_installed_providers(
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["read:providers"])
    ),
):
    tenant_id = authenticated_entity.tenant_id
    logger.info("Getting installed providers", extra={"tenant_id": tenant_id})
    providers = ProvidersFactory.get_all_providers()
    installed_providers = ProvidersFactory.get_installed_providers(
        tenant_id, providers, include_details=True
    )
    return JSONResponse(content=jsonable_encoder(installed_providers), status_code=200)


@router.get(
    "/{provider_type}/{provider_id}/configured-alerts",
    description="Get alerts configuration from a provider",
)
def get_alerts_configuration(
    provider_type: str,
    provider_id: str,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["read:providers"])
    ),
) -> list:
    tenant_id = authenticated_entity.tenant_id
    logger.info(
        "Getting provider alerts",
        extra={
            "tenant_id": tenant_id,
            "provider_type": provider_type,
            "provider_id": provider_id,
        },
    )
    context_manager = ContextManager(tenant_id=tenant_id)
    secret_manager = SecretManagerFactory.get_secret_manager(context_manager)
    provider_config = secret_manager.read_secret(
        f"{tenant_id}_{provider_type}_{provider_id}", is_json=True
    )
    provider = ProvidersFactory.get_provider(
        context_manager, provider_id, provider_type, provider_config
    )
    return provider.get_alerts_configuration()


@router.get(
    "/{provider_type}/{provider_id}/logs",
    description="Get logs from a provider",
)
def get_logs(
    provider_type: str,
    provider_id: str,
    limit: int = 5,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["read:providers"])
    ),
) -> list:
    try:
        tenant_id = authenticated_entity.tenant_id
        logger.info(
            "Getting provider logs",
            extra={
                "tenant_id": tenant_id,
                "provider_type": provider_type,
                "provider_id": provider_id,
            },
        )
        context_manager = ContextManager(tenant_id=tenant_id)
        secret_manager = SecretManagerFactory.get_secret_manager(context_manager)
        provider_config = secret_manager.read_secret(
            f"{tenant_id}_{provider_type}_{provider_id}", is_json=True
        )
        provider = ProvidersFactory.get_provider(
            context_manager, provider_id, provider_type, provider_config
        )
        return provider.get_logs(limit=limit)
    except HTTPException as e:
        raise e
    except ModuleNotFoundError:
        raise HTTPException(404, detail=f"Provider {provider_type} not found")
    except Exception:
        logger.exception(
            "Failed to get provider logs",
            extra={
                "tenant_id": tenant_id,
                "provider_type": provider_type,
                "provider_id": provider_id,
            },
        )
        return []


@router.get(
    "/{provider_type}/schema",
    description="Get the provider's API schema used to push alerts configuration",
)
def get_alerts_schema(
    provider_type: str,
) -> dict:
    try:
        logger.info(
            "Getting provider alerts schema", extra={"provider_type": provider_type}
        )
        provider = ProvidersFactory.get_provider_class(provider_type)
        return provider.get_alert_schema()
    except ModuleNotFoundError:
        raise HTTPException(404, detail=f"Provider {provider_type} not found")


@router.get(
    "/{provider_type}/{provider_id}/alerts/count",
    description="Get number of alerts a specific provider has received (in a specific time time period or ever)",
)
def get_alert_count(
    provider_type: str,
    provider_id: str,
    ever: bool,
    start_time: Optional[datetime.datetime] = None,
    end_time: Optional[datetime.datetime] = None,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["read:alert"])
    ),
):
    tenant_id = authenticated_entity.tenant_id
    if ever is False and (start_time is None or end_time is None):
        return HTTPException(
            status_code=400, detail="Missing start_time and/or end_time"
        )
    return ProviderAlertsCountResponseDTO(
        count=count_alerts(
            provider_type=provider_type,
            provider_id=provider_id,
            ever=ever,
            start_time=start_time,
            end_time=end_time,
            tenant_id=tenant_id,
        ),
    )


@router.post(
    "/{provider_type}/{provider_id}/alerts",
    description="Push new alerts to the provider",
)
def add_alert(
    provider_type: str,
    provider_id: str,
    alert: dict,
    alert_id: Optional[str] = None,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["write:alert"])
    ),
) -> JSONResponse:
    tenant_id = authenticated_entity.tenant_id
    logger.info(
        "Adding alert to provider",
        extra={
            "tenant_id": tenant_id,
            "provider_type": provider_type,
            "provider_id": provider_id,
        },
    )
    context_manager = ContextManager(tenant_id=tenant_id)
    secret_manager = SecretManagerFactory.get_secret_manager(context_manager)
    provider_config = secret_manager.read_secret(
        f"{tenant_id}_{provider_type}_{provider_id}", is_json=True
    )
    provider = ProvidersFactory.get_provider(
        context_manager, provider_id, provider_type, provider_config
    )
    try:
        provider.deploy_alert(alert, alert_id)
        return JSONResponse(status_code=200, content={"message": "deployed"})
    except Exception as e:
        return JSONResponse(status_code=500, content=e.args[0])


@router.post(
    "/test",
    description="Test a provider's alert retrieval",
)
def test_provider(
    provider_info: dict = Body(...),
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["read:providers"])
    ),
) -> JSONResponse:
    # Extract parameters from the provider_info dictionary
    # For now, we support only 1:1 provider_type:provider_id
    # In the future, we might want to support multiple providers of the same type
    tenant_id = authenticated_entity.tenant_id
    provider_id = provider_info.pop("provider_id")
    provider_type = provider_info.pop("provider_type", None) or provider_id
    logger.info(
        "Testing provider",
        extra={
            "provider_id": provider_id,
            "provider_type": provider_type,
            "tenant_id": tenant_id,
        },
    )
    provider_config = {
        "authentication": provider_info,
    }
    # TODO: valdiations:
    # 1. provider_type and provider id is valid
    # 2. the provider config is valid
    context_manager = ContextManager(
        tenant_id=tenant_id, workflow_id=""  # this is not in a workflow scope
    )
    provider = ProvidersFactory.get_provider(
        context_manager, provider_id, provider_type, provider_config
    )
    try:
        alerts = provider.get_alerts_configuration()
        return JSONResponse(status_code=200, content={"alerts": alerts})
    except GetAlertException as e:
        return JSONResponse(status_code=e.status_code, content=e.message)
    except Exception as e:
        return JSONResponse(status_code=400, content=str(e))


@router.delete("/{provider_type}/{provider_id}", description="Delete provider")
def delete_provider(
    provider_type: str,
    provider_id: str,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["delete:providers"])
    ),
    session: Session = Depends(get_session),
):
    tenant_id = authenticated_entity.tenant_id
    try:
        ProvidersService.delete_provider(tenant_id, provider_id, session)
        return JSONResponse(status_code=200, content={"message": "deleted"})
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"message": e.detail})
    except Exception as e:
        logger.exception("Failed to delete provider")
        return JSONResponse(status_code=400, content={"message": str(e)})


@router.post(
    "/{provider_id}/scopes",
    description="Validate provider scopes",
    status_code=200,
    response_model=dict[str, bool | str],
)
def validate_provider_scopes(
    provider_id: str,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["write:providers"])
    ),
    session: Session = Depends(get_session),
):
    tenant_id = authenticated_entity.tenant_id
    logger.info("Validating provider scopes", extra={"provider_id": provider_id})
    provider = session.exec(
        select(Provider).where(
            (Provider.tenant_id == tenant_id) & (Provider.id == provider_id)
        )
    ).one()

    if not provider:
        raise HTTPException(404, detail="Provider not found")

    context_manager = ContextManager(tenant_id=tenant_id)
    secret_manager = SecretManagerFactory.get_secret_manager(context_manager)
    provider_config = secret_manager.read_secret(
        provider.configuration_key, is_json=True
    )
    provider_instance = ProvidersFactory.get_provider(
        context_manager, provider_id, provider.type, provider_config
    )
    validated_scopes = provider_instance.validate_scopes()
    if validated_scopes != provider.validatedScopes:
        provider.validatedScopes = validated_scopes
        session.commit()
    logger.info(
        "Validated provider scopes",
        extra={"provider_id": provider_id, "validated_scopes": validated_scopes},
    )
    return validated_scopes


@router.put("/{provider_id}", description="Update provider", status_code=200)
async def update_provider(
    provider_id: str,
    request: Request,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["update:providers"])
    ),
    session: Session = Depends(get_session),
):
    tenant_id = authenticated_entity.tenant_id
    updated_by = authenticated_entity.email
    logger.info(
        "Updating provider",
        extra={"provider_id": provider_id, "tenant_id": tenant_id},
    )
    try:
        provider_info = await request.json()
    except Exception:
        form_data = await request.form()
        provider_info = dict(form_data)

    if not provider_info:
        raise HTTPException(status_code=400, detail="No valid data provided")

    for key, value in provider_info.items():
        if isinstance(value, UploadFile):
            provider_info[key] = value.file.read().decode()

    try:
        result = ProvidersService.update_provider(
            tenant_id, provider_id, provider_info, updated_by, session
        )
        return JSONResponse(status_code=200, content=result)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"message": e.detail})
    except Exception as e:
        logger.exception("Failed to update provider")
        return JSONResponse(status_code=400, content={"message": str(e)})


@router.post("/install", description="Install provider")
async def install_provider(
    request: Request,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["write:providers"])
    ),
):
    tenant_id = authenticated_entity.tenant_id
    installed_by = authenticated_entity.email

    try:
        provider_info = await request.json()
    except Exception:
        form_data = await request.form()
        provider_info = dict(form_data)

    if not provider_info:
        raise HTTPException(status_code=400, detail="No valid data provided")

    try:
        provider_id = provider_info.pop("provider_id")
        provider_name = provider_info.pop("provider_name")
        provider_type = provider_info.pop("provider_type", None) or provider_id
        pulling_enabled = provider_info.pop("pulling_enabled", True)
    except KeyError as e:
        raise HTTPException(
            status_code=400, detail=f"Missing required field: {e.args[0]}"
        )

    for key, value in provider_info.items():
        if isinstance(value, UploadFile):
            provider_info[key] = value.file.read().decode()

    try:
        result = ProvidersService.install_provider(
            tenant_id,
            installed_by,
            provider_id,
            provider_name,
            provider_type,
            provider_info,
            pulling_enabled=pulling_enabled,
        )
        return JSONResponse(status_code=200, content=result)
    except HTTPException as e:
        if e.status_code == 412:
            logger.error(
                "Failed to validate mandatory provider scopes, returning 412",
                extra={
                    "provider_id": provider_id,
                    "provider_type": provider_type,
                    "tenant_id": tenant_id,
                },
            )
        raise
    except Exception as e:
        logger.exception(
            "Failed to install provider",
            extra={
                "provider_id": provider_id,
                "provider_type": provider_type,
                "tenant_id": tenant_id,
            },
        )
        return JSONResponse(status_code=400, content={"message": str(e)})


@router.post(
    "/install/oauth2/{provider_type}", description="Install provider via oauth2."
)
async def install_provider_oauth2(
    provider_type: str,
    provider_info: dict = Body(...),
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["write:providers"])
    ),
    session: Session = Depends(get_session),
):
    tenant_id = authenticated_entity.tenant_id
    installed_by = authenticated_entity.email
    provider_unique_id = uuid.uuid4().hex
    logger.info(
        "Installing provider",
        extra={
            "provider_id": provider_unique_id,
            "provider_type": provider_type,
            "tenant_id": tenant_id,
        },
    )
    try:
        provider_class = ProvidersFactory.get_provider_class(provider_type)
        install_webhook = provider_info.pop("install_webhook", "true") == "true"
        pulling_enabled = provider_info.pop("pulling_enabled", "true") == "true"
        provider_info = provider_class.oauth2_logic(**provider_info)
        provider_name = provider_info.pop(
            "provider_name", f"{provider_unique_id}-oauth2"
        )
        provider_name = provider_name.lower().replace(" ", "").replace("_", "-")
        provider_config = {
            "authentication": provider_info,
            "name": provider_name,
        }
        # Instantiate the provider object and perform installation process
        context_manager = ContextManager(tenant_id=tenant_id)
        provider = ProvidersFactory.get_provider(
            context_manager, provider_unique_id, provider_type, provider_config
        )

        validated_scopes = ProvidersService.validate_scopes(provider)

        secret_manager = SecretManagerFactory.get_secret_manager(context_manager)
        secret_name = f"{tenant_id}_{provider_type}_{provider_unique_id}"
        secret_manager.write_secret(
            secret_name=secret_name,
            secret_value=json.dumps(provider_config),
        )
        # add the provider to the db
        provider = Provider(
            id=provider_unique_id,
            tenant_id=tenant_id,
            name=provider_name,
            type=provider_type,
            installed_by=installed_by,
            installation_time=time.time(),
            configuration_key=secret_name,
            validatedScopes=validated_scopes,
            pulling_enabled=pulling_enabled,
        )
        session.add(provider)
        session.commit()

        if install_webhook:
            install_provider_webhook(
                provider_type, provider.id, authenticated_entity, session
            )

        return JSONResponse(
            status_code=200,
            content={
                "type": provider_type,
                "id": provider_unique_id,
                "details": provider_config,
            },
        )
    except Exception as e:
        logger.exception(
            "Failed to install provider",
            extra={
                "provider_id": provider_unique_id,
                "provider_type": provider_type,
                "tenant_id": tenant_id,
            },
        )
        raise HTTPException(status_code=400, detail=str(e))


def _get_provider(tenant_id: str, provider_id: str, session: Session):
    """
    Get provider configuration from database or default providers.

    Returns:
        dict: Contains provider_id, provider_type, config
    """
    context_manager = ContextManager(tenant_id=tenant_id)

    if provider_id.startswith("default-"):
        try:
            provider_type = provider_id.split("-")[1]
            return ProvidersFactory.get_provider(
                context_manager,
                provider_id,
                provider_type,
                {"authentication": {}},  # default providers shouldn't have auth config
            )
        except IndexError:
            raise HTTPException(
                400,
                detail="Default provider must be in the format default-<provider_type>",
            )

    secret_manager = SecretManagerFactory.get_secret_manager(context_manager)

    try:
        # Try to get provider from database
        provider = session.exec(
            select(Provider).where(
                (Provider.tenant_id == tenant_id) & (Provider.id == provider_id)
            )
        ).one()

        provider_config = secret_manager.read_secret(
            provider.configuration_key, is_json=True
        )

        return ProvidersFactory.get_provider(
            context_manager, provider.id, provider.type, provider_config
        )

    except NoResultFound as e:
        raise HTTPException(404, detail="Provider not found") from e


@router.post(
    "/{provider_id}/invoke/{method}",
    description="Invoke provider special method",
    status_code=200,
)
def invoke_provider_method(
    provider_id: str,
    method: str,
    body: dict = Body(...),
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["write:providers"])
    ),
    session: Session = Depends(get_session),
):
    tenant_id = authenticated_entity.tenant_id
    logger.info(
        "Invoking provider method", extra={"provider_id": provider_id, "method": method}
    )

    try:
        provider_instance = _get_provider(tenant_id, provider_id, session)

        # Check if method exists
        func: Callable | None = getattr(provider_instance, method, None)
        if not func:
            raise HTTPException(400, detail="Method not found")

        # Invoke the method with the body as params
        response = func(**body)

        logger.info(
            "Successfully invoked provider method",
            extra={
                "provider_id": provider_instance.provider_id,
                "provider_type": provider_instance.provider_type,
                "method": method,
            },
        )
        return response

    except ProviderConfigurationException as e:
        logger.exception(
            "Failed to initialize provider",
            extra={"provider_id": provider_id, "method": method},
        )
        raise HTTPException(status_code=400, detail=str(e)) from e

    except ProviderMethodException as e:
        logger.exception(
            "Failed to invoke method",
            extra={"provider_id": provider_id, "method": method},
        )
        raise HTTPException(status_code=e.status_code, detail=e.message) from e

    except ProviderException as e:
        logger.exception(
            "Failed to invoke method",
            extra={"provider_id": provider_id, "method": method},
        )
        raise HTTPException(status_code=400, detail=str(e)) from e

    except (ValueError, TypeError) as e:
        logger.exception(
            "Invalid request parameters",
            extra={"provider_id": provider_id, "method": method},
        )
        raise HTTPException(status_code=400, detail=str(e)) from e

    except HTTPException:
        # Re-raise HTTPExceptions without modification (from _get_provider_configuration)
        raise

    except Exception as e:
        logger.exception(
            "Unexpected error while invoking provider method",
            extra={
                "provider_id": provider_id,
                "method": method,
                "method_params": body,
            },
        )
        raise HTTPException(status_code=500, detail="Internal server error") from e


# Webhook related endpoints
@router.post(
    "/install/webhook/{provider_type}/{provider_id}",
    description="Install webhook for a provider.",
)
def install_provider_webhook(
    provider_type: str,
    provider_id: str,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["write:providers"])
    ),
    session: Session = Depends(get_session),
):
    tenant_id = authenticated_entity.tenant_id
    webhook_installed = ProvidersService.install_webhook(
        tenant_id, provider_type, provider_id, session
    )
    if webhook_installed:
        return JSONResponse(status_code=200, content={"message": "webhook installed"})
    else:
        return JSONResponse(
            status_code=400, content={"message": "provider does not support webhook"}
        )


@router.get("/{provider_type}/webhook", description="Get provider's webhook settings.")
def get_webhook_settings(
    provider_type: str,
    provider_id: str | None = None,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["read:providers"])
    ),
    session: Session = Depends(get_session),
) -> ProviderWebhookSettings:
    tenant_id = authenticated_entity.tenant_id
    logger.info("Getting webhook settings", extra={"provider_type": provider_type})
    api_url = config("KEEP_API_URL")
    keep_webhook_api_url = f"{api_url}/alerts/event/{provider_type}"

    if provider_id:
        keep_webhook_api_url = f"{keep_webhook_api_url}?provider_id={provider_id}"

    provider_class = ProvidersFactory.get_provider_class(provider_type)
    webhook_api_key = get_or_create_api_key(
        session=session,
        tenant_id=tenant_id,
        created_by="system",
        unique_api_key_id="webhook",
        system_description="Webhooks API key",
    )
    # for cases where we need webhook with auth
    keep_webhook_api_url_with_auth = keep_webhook_api_url.replace(
        "https://", f"https://keep:{webhook_api_key}@"
    )

    try:
        webhookMarkdown = provider_class.webhook_markdown.format(
            keep_webhook_api_url=keep_webhook_api_url,
            api_key=webhook_api_key,
            keep_webhook_api_url_with_auth=keep_webhook_api_url_with_auth,
        )
    except AttributeError:
        webhookMarkdown = None

    logger.info("Got webhook settings", extra={"provider_type": provider_type})
    return ProviderWebhookSettings(
        webhookDescription=provider_class.webhook_description.format(
            keep_webhook_api_url=keep_webhook_api_url,
            api_key=webhook_api_key,
            keep_webhook_api_url_with_auth=keep_webhook_api_url_with_auth,
        ),
        webhookTemplate=provider_class.webhook_template.format(
            keep_webhook_api_url=keep_webhook_api_url,
            api_key=webhook_api_key,
            keep_webhook_api_url_with_auth=keep_webhook_api_url_with_auth,
        ),
        webhookMarkdown=webhookMarkdown,
    )


@router.post("/healthcheck", description="Run healthcheck on a provider")
async def healthcheck_provider(
    request: Request,
) -> Dict[str, Any]:
    try:
        provider_info = await request.json()
    except Exception:
        form_data = await request.form()
        provider_info = dict(form_data)

    if not provider_info:
        raise HTTPException(status_code=400, detail="No valid data provided")

    try:
        provider_id = provider_info.pop("provider_id")
        provider_type = provider_info.pop("provider_type", None) or provider_id
        provider_name = f"{provider_type} healthcheck"
    except KeyError as e:
        raise HTTPException(
            status_code=400, detail=f"Missing required field: {e.args[0]}"
        )

    for key, value in provider_info.items():
        if isinstance(value, UploadFile):
            provider_info[key] = value.file.read().decode()

    provider = ProvidersService.prepare_provider(
        provider_id,
        provider_name,
        provider_type,
        provider_info,
    )

    result = provider.get_health_report()
    return result


@router.get("/healthcheck", description="Get all providers for healthcheck")
def get_healthcheck_providers():
    logger.info("Getting all providers for healthcheck")
    providers = ProvidersService.get_all_providers()

    healthcheck_providers = [provider for provider in providers if provider.health]

    is_localhost = _is_localhost()

    return {
        "providers": healthcheck_providers,
        "is_localhost": is_localhost,
    }
