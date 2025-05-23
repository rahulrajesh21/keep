import { Provider } from "@/shared/api/providers";

export const mockProviders: Provider[] = [
  {
    id: "clickhouse",
    type: "clickhouse",
    config: {},
    installed: true,
    linked: true,
    last_alert_received: "",
    details: {
      authentication: {},
    },
    display_name: "Mock Clickhouse Provider",
    can_query: true,
    query_params: ["query", "single_row"],
    can_notify: false,
    validatedScopes: {},
    tags: [],
    pulling_available: true,
    pulling_enabled: true,
    categories: [],
    coming_soon: false,
    health: false,
  },
  {
    id: "ntfy",
    type: "ntfy",
    config: {},
    installed: true,
    linked: true,
    can_query: false,
    can_notify: true,
    notify_params: ["message", "topic"],
    details: {
      authentication: {},
    },
    display_name: "Mock Ntfy Provider",
    validatedScopes: {},
    tags: [],
    pulling_available: true,
    pulling_enabled: true,
    last_alert_received: "",
    categories: [],
    coming_soon: false,
    health: false,
  },
  {
    id: "slack",
    type: "slack",
    config: {},
    installed: true,
    linked: true,
    last_alert_received: "",
    details: {
      authentication: {},
    },
    display_name: "Mock Slack Provider",
    can_query: false,
    can_notify: true,
    notify_params: ["message"],
    validatedScopes: {},
    tags: [],
    pulling_available: true,
    pulling_enabled: true,
    categories: [],
    coming_soon: false,
    health: false,
  },
  {
    id: "console",
    type: "console",
    config: {},
    installed: true,
    linked: true,
    last_alert_received: "",
    details: {
      authentication: {},
    },
    display_name: "Mock Console Provider",
    can_query: false,
    can_notify: true,
    notify_params: ["message"],
    validatedScopes: {},
    tags: [],
    pulling_available: true,
    pulling_enabled: true,
    categories: [],
    coming_soon: false,
    health: false,
  },
];
