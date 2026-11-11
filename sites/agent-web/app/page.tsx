import { getAppUser, appSignInPath, appSignOutPath, getAuthMode } from "./auth";
import AgentChat from "./components/AgentChat";
import {WorkspaceLocale} from "./components/WorkspaceLocale";

export const dynamic = "force-dynamic";
export default async function Home() {
  const user = await getAppUser();
  return <WorkspaceLocale><AgentChat key={user ? `user:${user.userId}` : "guest"} signedIn={Boolean(user)} ownerId={user?.userId||null} displayName={user?.displayName || "Guest"}
    localAuth={getAuthMode()==='local'} signInPath={appSignInPath("/")} signOutPath={appSignOutPath()} /></WorkspaceLocale>;
}
