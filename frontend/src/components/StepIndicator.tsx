import type { PlannerStep } from "../plannerFlow";

const visibleSteps: { id: Exclude<PlannerStep, "home">; title: string }[] = [
  { id: "route", title: "여정 선택" },
  { id: "preferences", title: "나들이 조건" },
  { id: "result", title: "추천 결과" },
];

export function StepIndicator({ step }: { step: Exclude<PlannerStep, "home"> }) {
  return (
    <nav className="step-indicator" aria-label="나들이 추천 진행 단계">
      <ol>
        {visibleSteps.map((item, index) => {
          const current = item.id === step;
          const completed = visibleSteps.findIndex((entry) => entry.id === step) > index;
          return (
            <li
              key={item.id}
              className={`${current ? "current" : ""} ${completed ? "completed" : ""}`.trim()}
              aria-current={current ? "step" : undefined}
            >
              <span className="step-number">{completed ? "✓" : index + 1}</span>
              <span>{item.title}</span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}