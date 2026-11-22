import { useEffect, useId, useRef, useState } from "react";
import { PhoneIcon } from "./icons";

// On phones the tel: link dials straight away. Desktop browsers usually
// can't dial, so a fine pointer (mouse) opens a popover with the number
// in large type instead; it still contains a tel: link for desktops that
// do have a dialer (phone-link apps).
function canDialDirectly(): boolean {
  return window.matchMedia?.("(pointer: coarse)").matches ?? false;
}

export function EmergencyButton() {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLAnchorElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const callRef = useRef<HTMLAnchorElement>(null);
  const titleId = useId();

  const close = (returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) buttonRef.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    callRef.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(true);
    };
    const onPointer = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!popoverRef.current?.contains(target) && !buttonRef.current?.contains(target)) {
        close(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  return (
    <span className="emergency">
      <a
        ref={buttonRef}
        className="btn-108"
        href="tel:108"
        aria-label="Emergency: call 108 ambulance"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={(e) => {
          if (canDialDirectly()) return;
          e.preventDefault();
          setOpen((o) => !o);
        }}
      >
        <PhoneIcon />
        108
      </a>

      {open && (
        <div
          ref={popoverRef}
          className="emergency-popover"
          role="dialog"
          aria-labelledby={titleId}
          onKeyDown={(e) => {
            // Keep Tab inside the popover while it is open.
            if (e.key !== "Tab") return;
            const items = popoverRef.current?.querySelectorAll<HTMLElement>("a, button");
            if (!items?.length) return;
            const first = items[0];
            const last = items[items.length - 1];
            if (e.shiftKey && document.activeElement === first) {
              e.preventDefault();
              last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
              e.preventDefault();
              first.focus();
            }
          }}
        >
          <p id={titleId} className="emergency-title">
            आपातकाल? <span lang="en">Emergency?</span>
          </p>

          <a ref={callRef} className="emergency-number" href="tel:108">
            <PhoneIcon />
            108
          </a>

          <p className="emergency-text">
            मुफ़्त एम्बुलेंस। फ़ोन से 108 पर कॉल करें।
            <span lang="en">Free ambulance. Call 108 from your phone.</span>
          </p>

          <p className="emergency-text">
            108 न लगे तो <a href="tel:112">112</a> पर कॉल करें।
            <span lang="en">
              If 108 doesn't connect, call <a href="tel:112">112</a>.
            </span>
          </p>

          <button type="button" className="btn-outline" onClick={() => close(true)}>
            बंद करें · Close
          </button>
        </div>
      )}
    </span>
  );
}
