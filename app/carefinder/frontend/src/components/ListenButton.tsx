import { SpeakerIcon } from "./icons";

interface Props {
  speaking: boolean;
  onListen: () => void;
  onStop: () => void;
}

// Reads the latest reply aloud on demand (replies to voice requests are
// also read automatically unless muted).
export function ListenButton({ speaking, onListen, onStop }: Props) {
  return (
    <div className="listen">
      <button type="button" className="btn-text listen-btn" onClick={speaking ? onStop : onListen}>
        <SpeakerIcon />
        {speaking ? "रोकें · Stop" : "सुनें · Listen"}
      </button>
    </div>
  );
}
