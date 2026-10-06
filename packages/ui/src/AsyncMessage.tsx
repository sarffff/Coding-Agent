import type { ReactNode } from "react";

export function AsyncMessage({ children, tone = "empty", onRetry, retryLabel }: { children: ReactNode; tone?: "error" | "loading" | "empty"; onRetry?: () => void; retryLabel?: string }) {
  return <div className={"async-message async-message--" + tone} role={tone === "error" ? "alert" : "status"}>
    <span>{children}</span>
    {onRetry && retryLabel ? <button type="button" className="text-button" onClick={onRetry}>{retryLabel}</button> : null}
  </div>;
}
