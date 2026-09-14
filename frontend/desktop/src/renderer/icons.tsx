import type { JSX } from "react";

export type IconName =
  | "sparkles" | "settings" | "plus" | "search" | "send" | "refresh"
  | "folder" | "upload" | "paperclip" | "check" | "x" | "clock" | "stop"
  | "activity" | "file" | "terminal" | "shield" | "layers" | "more" | "message" | "arrow" | "external" | "trash" | "globe";

type IconProps = { name: IconName; size?: number; title?: string; className?: string };

/** Dependency-free inline symbols for product controls and status, with no font or network dependency. */
export function Icon({ name, size = 18, title, className }: IconProps): JSX.Element {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  const shapes: Record<IconName, JSX.Element> = {
    sparkles: <><path {...common} d="m12 3-1.2 5.8L5 10l5.8 1.2L12 17l1.2-5.8L19 10l-5.8-1.2L12 3Z" /><path {...common} d="m5 15-.6 2.4L2 18l2.4.6L5 21l.6-2.4L8 18l-2.4-.6L5 15Z" /></>,
    settings: <><circle {...common} cx="12" cy="12" r="3" /><path {...common} d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.12 2.12-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V20.3h-3v-.08A1.7 1.7 0 0 0 10.68 18.66a1.7 1.7 0 0 0-1.88.34l-.06.06-2.12-2.12.06-.06A1.7 1.7 0 0 0 7.02 15a1.7 1.7 0 0 0-1.56-1.03h-.08v-3h.08A1.7 1.7 0 0 0 7.02 9.94 1.7 1.7 0 0 0 6.68 8.06L6.62 8l2.12-2.12.06.06a1.7 1.7 0 0 0 1.88.34A1.7 1.7 0 0 0 11.7 4.72v-.08h3v.08a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.88-.34l.06-.06L19.8 8l-.06.06a1.7 1.7 0 0 0-.34 1.88 1.7 1.7 0 0 0 1.56 1.03h.08v3h-.08A1.7 1.7 0 0 0 19.4 15Z" /></>,
    plus: <path {...common} d="M12 5v14M5 12h14" />,
    search: <><circle {...common} cx="10.8" cy="10.8" r="5.8" /><path {...common} d="m16 16 4 4" /></>,
    send: <><path {...common} d="m21 3-7.2 18-3.6-7.2L3 10.2 21 3Z" /><path {...common} d="m10.2 13.8 4.6-4.6" /></>,
    refresh: <><path {...common} d="M20 11a8 8 0 0 0-14.8-3.8L3 10M3 4v6h6" /><path {...common} d="M4 13a8 8 0 0 0 14.8 3.8L21 14m0 6v-6h-6" /></>,
    folder: <path {...common} d="M3 6.5A2.5 2.5 0 0 1 5.5 4H10l2 2h6.5A2.5 2.5 0 0 1 21 8.5v9A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5v-11Z" />,
    upload: <><path {...common} d="M12 16V4M7.5 8.5 12 4l4.5 4.5M5 20h14" /></>,
    paperclip: <path {...common} d="m8.5 12.5 6.6-6.6a3 3 0 1 1 4.2 4.2l-8.5 8.5a5 5 0 1 1-7.1-7.1L12 3.2" />,
    check: <path {...common} d="m5 12 4.2 4.2L19 6.5" />,
    x: <path {...common} d="m6 6 12 12M18 6 6 18" />,
    clock: <><circle {...common} cx="12" cy="12" r="8" /><path {...common} d="M12 7v5l3 2" /></>,
    stop: <rect {...common} x="6.5" y="6.5" width="11" height="11" rx="1.5" />,
    activity: <path {...common} d="M3 12h4l2-6 4 12 2-6h6" />,
    file: <><path {...common} d="M6 3h8l4 4v14H6z" /><path {...common} d="M14 3v5h5M9 13h6M9 17h4" /></>,
    terminal: <path {...common} d="m5 7 4 5-4 5M12 18h7" />,
    shield: <><path {...common} d="M12 3 5 6v5c0 4.6 2.8 8 7 10 4.2-2 7-5.4 7-10V6l-7-3Z" /><path {...common} d="m9 12 2 2 4-4" /></>,
    layers: <><path {...common} d="m12 3 9 5-9 5-9-5 9-5Z" /><path {...common} d="m3 12 9 5 9-5M3 16l9 5 9-5" /></>,
    more: <><circle fill="currentColor" cx="5" cy="12" r="1.4" /><circle fill="currentColor" cx="12" cy="12" r="1.4" /><circle fill="currentColor" cx="19" cy="12" r="1.4" /></>,
    message: <path {...common} d="M20 11.5a7.5 7.5 0 0 1-8 7.5 8.5 8.5 0 0 1-3.4-.7L4 20l1.5-4A7.4 7.4 0 0 1 4 11.5 7.5 7.5 0 0 1 12 4a7.5 7.5 0 0 1 8 7.5Z" />,
    arrow: <path {...common} d="M5 12h14M13 6l6 6-6 6" />,
    external: <><path {...common} d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><path {...common} d="M15 3h6v6M10 14 21 3" /></>,
    trash: <><path {...common} d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></>,
    globe: <><circle {...common} cx="12" cy="12" r="9" /><path {...common} d="M3.6 9h16.8M3.6 15h16.8M12 3a14 14 0 0 0 0 18M12 3a14 14 0 0 1 0 18" /></>
  };
  return <svg className={className} width={size} height={size} viewBox="0 0 24 24" aria-hidden={title ? undefined : true} role={title ? "img" : undefined}>{title ? <title>{title}</title> : null}{shapes[name]}</svg>;
}
