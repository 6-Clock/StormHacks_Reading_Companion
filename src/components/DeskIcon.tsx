import type { SVGProps } from "react";

type IconName = "book" | "eye" | "mic" | "send" | "play" | "stop" | "scan" | "volume" | "spark";

export function DeskIcon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
    {name === "book" && <><path d="M12 5.5C8 3 4 3.5 2 4.5v15C5 18 9 18 12 20c3-2 7-2 10-.5v-15c-2-1-6-1.5-10 1Z" /><path d="M12 5.5V20" /></>}
    {name === "eye" && <><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></>}
    {name === "mic" && <><rect x="9" y="2" width="6" height="13" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8" /></>}
    {name === "send" && <><path d="m3 10 18-7-7 18-3-8-8-3Z" /><path d="m11 13 10-10" /></>}
    {name === "play" && <path d="m8 4 13 8-13 8V4Z" />}
    {name === "stop" && <rect x="5" y="5" width="14" height="14" rx="1" />}
    {name === "scan" && <><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M7 8h10M7 12h10M7 16h7" /></>}
    {name === "volume" && <><path d="m11 4-6 5H2v6h3l6 5V4ZM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14" /></>}
    {name === "spark" && <path d="M12 2c1 7 3 9 10 10-7 1-9 3-10 10-1-7-3-9-10-10 7-1 9-3 10-10Z" />}
  </svg>;
}
