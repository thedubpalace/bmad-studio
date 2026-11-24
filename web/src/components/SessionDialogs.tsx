import { useEffect, useRef } from "react";

import { useSession } from "../auth/SessionContext";
import { th } from "../i18n/th";
import { Icon } from "./Icon";
import { LoginForm } from "./LoginForm";

/** session หมดอายุกลางทาง: modal login ทับหน้าเดิม — หน้าข้างหลังไม่ถูก unmount จึงไม่ทิ้ง state */
export function SessionExpiredModal() {
  const { expired } = useSession();
  const dialogRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<Element | null>(null);

  useEffect(() => {
    if (expired) {
      openerRef.current = document.activeElement;
    } else if (openerRef.current instanceof HTMLElement) {
      openerRef.current.focus();
      openerRef.current = null;
    }
  }, [expired]);

  useEffect(() => {
    if (!expired) return;
    const dialog = dialogRef.current;
    function trap(event: KeyboardEvent) {
      if (event.key !== "Tab" || !dialog) return;
      const items = dialog.querySelectorAll<HTMLElement>("input, button, a[href]");
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", trap);
    return () => document.removeEventListener("keydown", trap);
  }, [expired]);

  if (!expired) return null;
  return (
    <div className="overlay">
      <div ref={dialogRef} className="dialog surface-solid" role="dialog" aria-modal="true" aria-labelledby="expired-title">
        <h2 id="expired-title" className="dialog__title">
          {th.session.expiredTitle}
        </h2>
        <p className="dialog__body">{th.session.expiredBody}</p>
        <LoginForm />
      </div>
    </div>
  );
}

/** 403 csrf_invalid: แจ้ง "เซสชันไม่ตรงกัน โหลดหน้าใหม่" โดยไม่ทิ้ง draft (ผู้ใช้เลือกโหลดเอง) */
export function CsrfBanner() {
  const { csrfMismatch } = useSession();
  if (!csrfMismatch) return null;
  return (
    <div className="banner banner--warn" role="alert">
      <Icon name="alert" />
      <span>{th.session.csrfMismatch}</span>
      <button type="button" className="button button--secondary" onClick={() => window.location.reload()}>
        {th.session.reload}
      </button>
    </div>
  );
}
