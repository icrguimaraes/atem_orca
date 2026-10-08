import { useCallback, useEffect, useRef, useState, type PointerEvent } from "react";
import { Alert, Modal } from "../ui";

/* Recorte quadrado da foto do contratado PJ antes de enviar (mesmo editor do organograma do Movimentação de Pessoal):
   arraste para deslocar, controle para aproximar; grava JPEG 280×280. */

const PREVIEW = 260; // px do canvas de prévia
const OUT = 280; // JPEG final: 280×280
const QUALITY = 0.85;
const MAX_ZOOM = 4;

interface Crop {
  zoom: number; // 1 = lado menor da imagem inteiro no quadrado
  cx: number; // centro do recorte em px da imagem
  cy: number;
}

/** Quadrado de recorte (em px da imagem) já limitado às bordas. */
function cropRect(img: HTMLImageElement, c: Crop) {
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  const v = Math.min(w, h) / c.zoom;
  const x = Math.min(Math.max(c.cx - v / 2, 0), w - v);
  const y = Math.min(Math.max(c.cy - v / 2, 0), h - v);
  return { x, y, v };
}

function clampCrop(img: HTMLImageElement, c: Crop): Crop {
  const r = cropRect(img, c);
  return { zoom: c.zoom, cx: r.x + r.v / 2, cy: r.y + r.v / 2 };
}

function draw(canvas: HTMLCanvasElement, img: HTMLImageElement, c: Crop, size: number) {
  canvas.width = size;
  canvas.height = size;
  const g = canvas.getContext("2d");
  if (!g) return;
  g.fillStyle = "#ffffff"; // JPEG não tem transparência
  g.fillRect(0, 0, size, size);
  g.imageSmoothingQuality = "high";
  const r = cropRect(img, c);
  g.drawImage(img, r.x, r.y, r.v, r.v, 0, 0, size, size);
}

/** `source` é uma URL de objeto (arquivo novo) ou o data URL da foto atual ("Ajustar"). */
export function PhotoEditor({ source, onClose, onSave }: { source: string; onClose: () => void; onSave: (jpeg: Blob) => Promise<boolean> }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  const [crop, setCrop] = useState<Crop>({ zoom: 1, cx: 0, cy: 0 });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const drag = useRef<{ x: number; y: number; crop: Crop } | null>(null);

  useEffect(() => {
    const image = new Image();
    image.onload = () => {
      const w = image.naturalWidth;
      const h = image.naturalHeight;
      const v = Math.min(w, h);
      // retrato: recorte mais perto do topo (rosto)
      const cy = h > w ? (h - v) * 0.25 + v / 2 : h / 2;
      setImg(image);
      setCrop({ zoom: 1, cx: w / 2, cy });
    };
    image.onerror = () => setError("Não foi possível abrir a imagem. Use um arquivo JPEG, PNG ou WebP.");
    image.src = source;
  }, [source]);

  useEffect(() => {
    if (img && canvasRef.current) draw(canvasRef.current, img, crop, PREVIEW);
  }, [img, crop]);

  const setZoom = useCallback(
    (z: number) => {
      if (!img) return;
      setCrop((c) => clampCrop(img, { ...c, zoom: Math.min(Math.max(z, 1), MAX_ZOOM) }));
    },
    [img],
  );

  function onPointerDown(e: PointerEvent<HTMLCanvasElement>) {
    if (!img) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, crop };
  }
  function onPointerMove(e: PointerEvent<HTMLCanvasElement>) {
    const d = drag.current;
    if (!d || !img) return;
    const shown = e.currentTarget.getBoundingClientRect().width || PREVIEW;
    const r = cropRect(img, d.crop);
    const k = r.v / shown; // px da imagem por px de tela
    setCrop(clampCrop(img, { zoom: d.crop.zoom, cx: d.crop.cx - (e.clientX - d.x) * k, cy: d.crop.cy - (e.clientY - d.y) * k }));
  }
  function onPointerUp() {
    drag.current = null;
  }

  function nudge(dx: number, dy: number) {
    if (!img) return;
    const r = cropRect(img, crop);
    setCrop(clampCrop(img, { ...crop, cx: crop.cx + dx * r.v * 0.05, cy: crop.cy + dy * r.v * 0.05 }));
  }

  async function save() {
    if (!img) return;
    setBusy(true);
    setError(null);
    const out = document.createElement("canvas");
    draw(out, img, crop, OUT);
    const blob = await new Promise<Blob | null>((resolve) => out.toBlob(resolve, "image/jpeg", QUALITY));
    if (!blob) {
      setBusy(false);
      setError("Não foi possível gerar a imagem.");
      return;
    }
    const ok = await onSave(blob);
    setBusy(false);
    if (ok) onClose();
  }

  return (
    <Modal
      title="Recortar foto"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>Cancelar</button>
          <button type="button" className="btn btn-primary" disabled={!img || busy} onClick={save}>
            {busy ? "Enviando…" : "Usar esta foto"}
          </button>
        </>
      }
    >
      <div className="photo-editor">
        {error && <Alert>{error}</Alert>}
        <canvas
          ref={canvasRef}
          width={PREVIEW}
          height={PREVIEW}
          tabIndex={0}
          aria-label="Prévia do recorte: arraste para deslocar; setas também deslocam"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={(e) => {
            const map: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
            const d = map[e.key];
            if (d) {
              e.preventDefault();
              nudge(d[0], d[1]);
            }
          }}
        />
        <div className="photo-zoom">
          <button type="button" className="btn btn-ghost btn-sm" aria-label="Afastar" disabled={!img} onClick={() => setZoom(crop.zoom - 0.25)}>−</button>
          <input type="range" min={1} max={MAX_ZOOM} step={0.01} value={crop.zoom} disabled={!img} aria-label="Aproximação" onChange={(e) => setZoom(Number(e.target.value))} />
          <button type="button" className="btn btn-ghost btn-sm" aria-label="Aproximar" disabled={!img} onClick={() => setZoom(crop.zoom + 0.25)}>+</button>
        </div>
        <p className="muted small">Arraste a imagem para enquadrar o rosto. A foto é gravada em 280×280.</p>
      </div>
    </Modal>
  );
}
