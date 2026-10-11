import React, { useEffect, useLayoutEffect, useRef } from 'react';
import { FileText, Rocket, Settings2, Sparkles, X } from 'lucide-react';

// 프로젝트 and 노드 live on the 앱 관리 scene tab now.
const TABS = [
  { id: 'deploy', title: '새 배포', description: '소스와 배포 타깃을 선택하고 앱을 올려보세요.', Icon: Rocket },
  { id: 'details', title: '배포 상세', description: '분석 결과와 서비스 주소, 자가치유 패치를 한곳에서 확인하세요.', Icon: FileText },
  { id: 'settings', title: '설정', description: '서버 연결에 필요한 인증 정보를 관리하세요.', Icon: Settings2 },
];
const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]';

export default function DetailsModal({ panel, onPanel, onClose, children }) {
  const dialog = useRef(null);
  const body = useRef(null);
  const close = useRef(null);
  const onCloseRef = useRef(onClose);
  const backdropPress = useRef(false);
  const open = !!panel;
  const selected = TABS.find((tab) => tab.id === panel) || TABS[1];
  useEffect(() => { onCloseRef.current = onClose; });
  // Open before children's passive effects measure their visible scroll containers.
  useLayoutEffect(() => {
    if (!open) return undefined;
    const modal = dialog.current;
    const previous = document.activeElement;
    if (!modal.open) modal.showModal();
    close.current?.focus();
    const handleKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault(); e.stopPropagation(); onCloseRef.current();
      } else if (e.key === 'Tab') {
        const items = [...modal.querySelectorAll(FOCUSABLE)].filter((el) => el.getClientRects().length);
        const first = items[0]; const last = items.at(-1);
        if (e.shiftKey && (document.activeElement === first || !modal.contains(document.activeElement))) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))) { e.preventDefault(); first?.focus(); }
      }
    };
    // Child confirmations consume Escape first; the modal then consumes it before the victory window listener.
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('keydown', handleKey);
      modal.close();
      backdropPress.current = false;
      if (previous?.isConnected && !previous.closest('[inert]')) previous.focus();
    };
  }, [open]);

  useLayoutEffect(() => {
    if (panel) body.current.scrollTop = 0;
  }, [panel]);

  const outside = (e) => {
    const bounds = dialog.current.getBoundingClientRect();
    return e.target === dialog.current && (e.clientX < bounds.left || e.clientX > bounds.right || e.clientY < bounds.top || e.clientY > bounds.bottom);
  };

  // Keep sections mounted when closed: form drafts and pending input survive panel navigation.
  return (
    <dialog ref={dialog} className="details-modal" aria-labelledby="modal-title" aria-describedby="modal-description"
      onCancel={(e) => { e.preventDefault(); onCloseRef.current(); }}
      onClose={() => { if (!dialog.current.open) onCloseRef.current(); }}
      onPointerDown={(e) => { backdropPress.current = outside(e); }}
      onPointerCancel={() => { backdropPress.current = false; }}
      onClick={(e) => {
        const dismiss = backdropPress.current && outside(e);
        backdropPress.current = false;
        if (dismiss) onCloseRef.current();
      }}>
      <header className="modal-header">
        <div className="modal-heading">
          <span className="modal-emblem" aria-hidden="true"><selected.Icon size={22} /></span>
          <div>
            <span className="modal-eyebrow"><Sparkles size={12} aria-hidden="true" /> CLOUDMORPH</span>
            <h2 id="modal-title">{selected.title}</h2>
            <p id="modal-description">{selected.description}</p>
          </div>
        </div>
        <button ref={close} type="button" className="dashboard-button icon-button modal-close" aria-label="팝업 닫기" onClick={onClose}><X size={18} /></button>
      </header>
      <nav className="modal-tabs" aria-label="배포 팝업 메뉴">
        {TABS.map(({ id, title, Icon }) => (
          <button key={id} id={`modal-menu-${id}`} type="button" aria-current={panel === id ? 'page' : undefined}
            aria-controls={`modal-section-${id}`} onClick={() => onPanel(id)}>
            <Icon size={15} aria-hidden="true" />{title}
          </button>
        ))}
      </nav>
      <div ref={body} className="modal-body">{children}</div>
    </dialog>
  );
}
