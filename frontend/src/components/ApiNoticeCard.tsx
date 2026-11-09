import type { Notice } from "../pages/types";
import { WarningIcon } from "./icons";

export function ApiNoticeCard({
  notice,
  onRetry,
}: {
  notice: Notice;
  onRetry: () => void;
}) {
  return (
    <div className="api-notice" role="alert">
      <span className="api-notice-icon">
        <WarningIcon />
      </span>
      <div>
        <strong>{notice.title}</strong>
        <p>{notice.body}</p>
      </div>
      <button className="outline-button" type="button" onClick={onRetry}>
        다시 시도
      </button>
    </div>
  );
}
