import Image from "next/image";
import type { CapturePreview } from "@/lib/api";

export function ScanPreview({ preview }: { preview: CapturePreview | null }) {
  if (!preview) return <figure className="scan-preview"><div className="scan-photo-empty" role="img" aria-label="No book camera capture"><span>No capture</span></div></figure>;
  return <figure className="scan-preview"><div className="scan-photo"><Image className="scan-photo-image" src={preview.data_url} alt="Latest book camera capture" width={preview.width} height={preview.height} unoptimized /></div></figure>;
}
