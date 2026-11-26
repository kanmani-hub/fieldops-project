import { Redirect, Slot } from "expo-router";
import { useAuthStore } from "../../src/auth/authStore";
import { normalizeRole } from "../../src/auth/roles";

export default function HeadLayout() {
  const user = useAuthStore((state) => state.user);
  const initialized = useAuthStore((state) => state.initialized);
  if (!initialized) return null;
  if (!user) return <Redirect href="/login" />;
  if (normalizeRole(user.role) !== "head") return <Redirect href="/" />;
  return <Slot />;
}
