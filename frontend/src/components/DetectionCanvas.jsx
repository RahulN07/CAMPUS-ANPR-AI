import { useEffect, useRef, useState } from "react";
import { LuCamera, LuVideoOff, LuScanLine, LuUpload } from "react-icons/lu";
import { Spinner } from "./Loader";

export default function DetectionCanvas({ onCapture, scanning }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const fileInputRef = useRef(null);
  const [streamActive, setStreamActive] = useState(false);
  const [camError, setCamError] = useState(false);

  useEffect(() => {
    let stream;
    async function start() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          setStreamActive(true);
        }
      } catch {
        setCamError(true);
      }
    }
    start();
    return () => stream?.getTracks().forEach((t) => t.stop());
  }, []);

  function captureFromWebcam() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !streamActive) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (blob) onCapture(new File([blob], "capture.jpg", { type: "image/jpeg" }), "WEBCAM");
    }, "image/jpeg", 0.92);
  }

  function onFilePicked(e) {
    const file = e.target.files?.[0];
    if (file) onCapture(file, "UPLOAD");
    e.target.value = "";
  }

  return (
    <div className="relative rounded-2xl overflow-hidden bg-ink-950 aspect-video group">
      {!camError ? (
        <video ref={videoRef} autoPlay muted playsInline className="w-full h-full object-cover" />
      ) : (
        <div className="w-full h-full grid place-items-center text-slate-500 flex-col gap-2">
          <LuVideoOff size={32} />
          <p className="text-sm">Camera unavailable — upload an image instead</p>
        </div>
      )}
      <canvas ref={canvasRef} className="hidden" />

      <div className="absolute top-3 left-3 flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-black/40 backdrop-blur text-white text-xs font-medium">
        <span className={`h-1.5 w-1.5 rounded-full ${streamActive ? "bg-emerald-400 animate-pulseDot" : "bg-slate-400"}`} />
        {streamActive ? "Live" : "Offline"}
      </div>

      {scanning && (
        <div className="absolute inset-0 grid place-items-center bg-black/40">
          <div className="flex flex-col items-center gap-2 text-white">
            <Spinner size={26} className="text-white" />
            <p className="text-sm font-medium flex items-center gap-1.5">
              <LuScanLine size={16} /> Reading plate…
            </p>
          </div>
        </div>
      )}

      <div className="absolute bottom-3 inset-x-3 flex items-center justify-center gap-2">
        <button
          onClick={captureFromWebcam}
          disabled={!streamActive || scanning}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-white text-ink-950 text-sm font-semibold shadow-soft disabled:opacity-50 hover:bg-slate-100"
        >
          <LuCamera size={17} /> Capture & Detect
        </button>
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={scanning}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-white/10 text-white text-sm font-semibold backdrop-blur disabled:opacity-50 hover:bg-white/20"
        >
          <LuUpload size={17} /> Upload
        </button>
        <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={onFilePicked} />
      </div>
    </div>
  );
}
