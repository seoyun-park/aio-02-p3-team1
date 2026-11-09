import type { ItineraryRunResponse, Sailing } from "../api/types";

export type ViewState =
  | { phase: "idle" }
  | {
      phase: "loading";
      sailing: Sailing;
      groupId: string | null;
      runId: string | null;
      generationIndex: number;
    }
  | {
      phase: "done";
      sailing: Sailing;
      groupId: string;
      run: ItineraryRunResponse;
    }
  | {
      phase: "error";
      sailing: Sailing;
      groupId: string | null;
      runId: string | null;
      generationIndex: number;
      message: string;
      retryable: boolean;
    };

export type Notice = { title: string; body: string };
export type RequestPhase = "loading" | "ready" | "error";