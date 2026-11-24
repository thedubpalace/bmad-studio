import type { ReactNode } from "react";

import { Icon, type IconName } from "./Icon";

export function EmptyState({ title, body, icon = "inbox", action }: { title: string; body?: string; icon?: IconName; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <span className="empty-state__icon">
        <Icon name={icon} size={28} />
      </span>
      <h2 className="empty-state__title">{title}</h2>
      {body ? <p className="empty-state__body">{body}</p> : null}
      {action}
    </div>
  );
}
