import Image from "next/image";

import type { CapturePreview } from "@/lib/api";

export function ScanPreview({ preview }: { preview: CapturePreview | null }) {
  if (!preview || preview.width <= 0 || preview.height <= 0 || !/^data:image\/(jpeg|png|webp);base64,/.test(preview.data_url)) {
    return <figure className="scan-preview"><div className="scan-photo-empty" role="img" aria-label="No book camera capture"><span>No capture</span></div></figure>;
  }

  const words = preview.boxes_status === "available" ? preview.words.filter((word) =>
    [word.x, word.y, word.width, word.height].every(Number.isFinite) && word.x >= 0 && word.y >= 0 && word.x < 1 && word.y < 1 && word.width > 0 && word.height > 0,
  ) : [];

  return <figure className="scan-preview">
    <div className="scan-photo">
      <Image className="scan-photo-image" src={preview.data_url} alt="Latest book camera capture" width={preview.width} height={preview.height} unoptimized />
      {words.length > 0 && <svg className="scan-word-overlay" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
        {words.map((word, index) => <rect key={`${index}-${word.x}-${word.y}`} className="scan-word-box" x={word.x} y={word.y} width={Math.min(word.width, 1 - word.x)} height={Math.min(word.height, 1 - word.y)} fill="none" stroke="currentColor" strokeWidth="1" vectorEffect="non-scaling-stroke"><title>{word.text}</title></rect>)}
      </svg>}
    </div>
    <figcaption className="scan-preview-caption">{preview.boxes_status === "unavailable" ? "Word boxes unavailable" : words.length ? `${words.length} detected word ${words.length === 1 ? "box" : "boxes"}` : "No word boxes detected"}</figcaption>
  </figure>;
}
