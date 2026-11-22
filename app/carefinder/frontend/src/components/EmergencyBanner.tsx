import { PhoneIcon } from "./icons";

// Shown when the speech layer flags a life-threatening situation.
// It sits above everything else; the search still runs underneath.
export function EmergencyBanner({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div className="emergency-banner" role="alert">
      <div className="emergency-banner-text">
        <strong>यह आपातकाल लगता है। अभी 108 पर कॉल करें।</strong>
        <span lang="en">This sounds like an emergency. Call 108 now.</span>
        <span className="small">
          108 न लगे तो <a href="tel:112">112</a> · If 108 doesn't connect, call{" "}
          <a href="tel:112">112</a>
        </span>
      </div>
      <a className="emergency-banner-call" href="tel:108">
        <PhoneIcon />
        108
      </a>
      <button type="button" className="btn-text emergency-banner-dismiss" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  );
}
