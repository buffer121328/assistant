import { useEffect, useState, type JSX } from "react";
import { Icon } from "./icons.tsx";
import { filterPaletteItems, type PaletteItem } from "./command-palette.ts";

export type CommandPaletteModalProps = {
  isOpen: boolean;
  onClose: () => void;
  items: PaletteItem[];
};

export function CommandPaletteModal({ isOpen, onClose, items }: CommandPaletteModalProps): JSX.Element | null {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  const filtered = filterPaletteItems(items, query);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  useEffect(() => {
    if (!isOpen) {
      setQuery("");
      setSelectedIndex(0);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => (filtered.length > 0 ? (prev + 1) % filtered.length : 0));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => (filtered.length > 0 ? (prev - 1 + filtered.length) % filtered.length : 0));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const selected = filtered[selectedIndex];
      if (selected) {
        selected.action();
        onClose();
      }
    }
  };

  return (
    <div className="palette-modal-backdrop" onClick={onClose}>
      <div className="palette-modal-container" onClick={(e) => e.stopPropagation()} onKeyDown={handleKeyDown}>
        <div className="palette-search-header">
          <Icon name="search" size={18} />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索任务、快捷命令、产物或操作… (↑↓ 导航 · 回车确认 · Esc 退出)"
          />
          <kbd className="palette-esc-badge">ESC</kbd>
        </div>

        <div className="palette-results-list" role="listbox">
          {filtered.length === 0 ? (
            <div className="palette-empty">
              <span>没有找到与 “{query}” 相关的结果</span>
            </div>
          ) : (
            filtered.map((item, idx) => (
              <button
                key={item.id}
                type="button"
                className={idx === selectedIndex ? "palette-item-row selected" : "palette-item-row"}
                role="option"
                aria-selected={idx === selectedIndex}
                onClick={() => {
                  item.action();
                  onClose();
                }}
              >
                <div className="palette-item-icon">
                  <Icon name={item.icon as any} size={16} />
                </div>
                <div className="palette-item-text">
                  <strong>{item.title}</strong>
                  <small>{item.subtitle}</small>
                </div>
                {item.shortcut ? <span className="palette-shortcut-badge">{item.shortcut}</span> : null}
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
