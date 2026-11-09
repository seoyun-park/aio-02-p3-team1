export const plannerSteps = ["home", "route", "preferences", "result"] as const;

export type PlannerStep = (typeof plannerSteps)[number];
export type TimetablePhase = "loading" | "ready" | "error";

export function nextPlannerStep(step: PlannerStep): PlannerStep {
  const index = plannerSteps.indexOf(step);
  return plannerSteps[Math.min(index + 1, plannerSteps.length - 1)];
}

export function previousPlannerStep(step: PlannerStep): PlannerStep {
  const index = plannerSteps.indexOf(step);
  return plannerSteps[Math.max(index - 1, 0)];
}

export function canContinueFromRoute(
  fromStopId: string,
  toStopId: string,
  tripsPhase: TimetablePhase,
): boolean {
  return Boolean(
    fromStopId.trim() &&
      toStopId.trim() &&
      fromStopId !== toStopId &&
      tripsPhase === "ready",
  );
}

export function canContinueFromPreferences(sailingId: string): boolean {
  return Boolean(sailingId.trim());
}
