// Only allow known billing destinations; never navigate to a stored arbitrary URL.
export function pendingPlanDestination(): string | null {
  try {
    const intent = JSON.parse(sessionStorage.getItem("barcodenest_plan_intent") || "null");
    if (intent?.plan !== "CREATOR" && intent?.plan !== "STARTER" && intent?.plan !== "GROWTH" && intent?.plan !== "ONE_TIME") return null;
    const interval = intent.plan !== "CREATOR" && intent.interval === "year" ? "year" : "month";
    return `/billing/?plan=${intent.plan}&interval=${interval}`;
  } catch { return null; }
}
