import React, { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

// 프로젝트 and 노드 live on the 앱 관리 scene tab now.
const TABS = [['deploy', '새 배포'], ['details', '배포 상세'], ['settings', '설정']];
const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]';

export default function DetailsDrawer({ panel, onPanel, onClose, logsRequest, children }) {
  const drawer = useRef(null);
  const body = useRef(null);
  const close = useRef(null);
  const onCloseRef = useRef(onClose);
  const open = !!panel;
  useEffect(() => { onCloseRef.current = onClose; });
  useEffect(() => {
    if (!open) return undefined;
    const previous = document.activeElement;
    close.current?.focus();
    const handleKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault(); e.stopPropagation(); onCloseRef.current();
      } else if (e.key === 'Tab') {
        const items = [...drawer.current.querySelectorAll(FOCUSABLE)].filter((el) => el.getClientRects().length);
        const first = items[0]; const last = items.at(-1);
        if (e.shiftKey && (document.activeElement === first || !drawer.current.contains(document.activeElement))) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && (document.activeElement === last || !drawer.current.contains(document.activeElement))) { e.preventDefault(); first?.focus(); }
      }
    };
    // Child confirmations consume Escape first; the drawer then consumes it before the victory window listener.
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('keydown', handleKey);
      if (previous?.isConnected && !previous.closest('[inert]')) previous.focus();
    };
  }, [open]);

  useEffect(() => {
    if (panel !== 'details' || !logsRequest) return undefined;
    const frame = requestAnimationFrame(() => {
      const container = body.current;
      const logs = container?.querySelector('[data-deployment-logs]');
      if (!logs) return;
      container.scrollTop += logs.getBoundingClientRect().top - container.getBoundingClientRect().top;
    });
    return () => cancelAnimationFrame(frame);
  }, [panel, logsRequest]);

  // Keep sections mounted when closed: form drafts and pending input survive panel navigation.
  return (
    <div className="drawer-layer" hidden={!open}>
      <button className="drawer-backdrop" aria-label="패널 닫기" tabIndex={-1} onClick={onClose} />
      <aside ref={drawer} className="details-drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <header className="drawer-header"><h2 id="drawer-title">설정 및 상세 정보</h2><button ref={close} className="dashboard-button icon-button" aria-label="상세 패널 닫기" onClick={onClose}><X size={18} /></button></header>
        <nav className="drawer-tabs" aria-label="상세 패널 메뉴">{TABS.map(([id, label]) => <button key={id} aria-current={panel === id ? 'page' : undefined} onClick={() => onPanel(id)}>{label}</button>)}</nav>
        <div ref={body} className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}
