import { ReactNode } from "react";
import { NextAuthProvider } from "../auth-provider";
import { Mulish } from "next/font/google";
import { ToastContainer } from "react-toastify";
import Navbar from "components/navbar/Navbar";
import { TopologyPollingContextProvider } from "@/app/(keep)/topology/model/TopologyPollingContext";
import { getConfig } from "@/shared/lib/server/getConfig";
import { ConfigProvider } from "../config-provider";
import { PHProvider } from "../posthog-provider";
import ReadOnlyBanner from "@/components/banners/read-only-banner";
import { auth } from "@/auth";
import { ThemeScript, WatchUpdateTheme } from "@/shared/ui";
import "@/app/globals.css";
import "react-toastify/dist/ReactToastify.css";
import { PostHogPageView } from "@/shared/ui/PostHogPageView";
import { WorkflowModalProvider } from "@/features/workflows/manual-run-workflow";

// If loading a variable font, you don't need to specify the font weight
const mulish = Mulish({
  subsets: ["latin"],
  display: "swap",
});

type RootLayoutProps = {
  children: ReactNode;
};

export default async function RootLayout({ children }: RootLayoutProps) {
  const config = getConfig();
  const session = await auth();

  return (
    <html lang="en" className={`bg-gray-50 ${mulish.className}`}>
      <body className="h-screen flex flex-col lg:grid lg:grid-cols-[192px_30px_auto] xl:grid-cols-[220px_30px_auto] 2xl:grid-cols-[250px_30px_auto] lg:grid-rows-1 lg:has-[aside[data-minimized='true']]:grid-cols-[0px_30px_auto]">
        {/* ThemeScript must be the first thing to avoid flickering */}
        <ThemeScript />
        <ConfigProvider config={config}>
          <PHProvider>
            <NextAuthProvider session={session}>
              <TopologyPollingContextProvider>
                <WorkflowModalProvider>
                  {/* @ts-ignore-error Server Component */}
                  <PostHogPageView />
                  <Navbar />
                  {/* https://discord.com/channels/752553802359505017/1068089513253019688/1117731746922893333 */}
                  <main className="page-container flex flex-col col-start-3 overflow-auto">
                    {/* Add the banner here, before the navbar */}
                    {config.READ_ONLY && <ReadOnlyBanner />}
                    <div className="flex-1">{children}</div>
                    {/** footer */}
                    {process.env.GIT_COMMIT_HASH &&
                      process.env.SHOW_BUILD_INFO !== "false" && (
                        <div className="pointer-events-none opacity-80 w-full p-2 text-slate-400 text-xs">
                          <div className="w-full text-right">
                            Version: {process.env.KEEP_VERSION} | Build:{" "}
                            {process.env.GIT_COMMIT_HASH.slice(0, 6)}
                          </div>
                        </div>
                      )}
                    <ToastContainer />
                  </main>
                </WorkflowModalProvider>
              </TopologyPollingContextProvider>
            </NextAuthProvider>
          </PHProvider>
        </ConfigProvider>
        <WatchUpdateTheme />
      </body>
    </html>
  );
}
