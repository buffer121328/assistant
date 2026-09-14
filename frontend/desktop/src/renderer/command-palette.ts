export type PaletteItemType = "task" | "command" | "action" | "navigation";

export type PaletteItem = {
  id: string;
  type: PaletteItemType;
  title: string;
  subtitle: string;
  shortcut?: string;
  icon: string;
  keywords?: string[];
  action: () => void;
};

export function filterPaletteItems(items: PaletteItem[], query: string): PaletteItem[] {
  const q = (query || "").trim().toLowerCase();
  if (!q) return items;

  return items.filter((item) => {
    if (item.title.toLowerCase().includes(q)) return true;
    if (item.subtitle.toLowerCase().includes(q)) return true;
    if (item.shortcut && item.shortcut.toLowerCase().includes(q)) return true;
    if (item.keywords && item.keywords.some((k) => k.toLowerCase().includes(q))) return true;
    return false;
  });
}
