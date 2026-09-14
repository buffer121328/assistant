import type { LocalArtifact } from "./api.ts";

export type ArtifactCategory = "all" | "document" | "spreadsheet" | "image" | "other";

export const ARTIFACT_CATEGORIES: { value: ArtifactCategory; label: string; icon: string }[] = [
  { value: "all", label: "全部成果", icon: "layers" },
  { value: "document", label: "文档与方案", icon: "file" },
  { value: "spreadsheet", label: "表格与数据", icon: "activity" },
  { value: "image", label: "图片与图表", icon: "sparkles" },
  { value: "other", label: "其他资料", icon: "folder" }
];

/**
 * Classifies an artifact into user-friendly category by filename and media_type.
 */
export function classifyArtifact(artifact: LocalArtifact): ArtifactCategory {
  const media = (artifact.media_type || "").toLowerCase();
  const filename = (artifact.filename || "").toLowerCase();

  if (
    media.startsWith("image/") ||
    filename.endsWith(".png") ||
    filename.endsWith(".jpg") ||
    filename.endsWith(".jpeg") ||
    filename.endsWith(".svg") ||
    filename.endsWith(".webp") ||
    artifact.generation_method === "screenshot" ||
    artifact.generation_method === "screen"
  ) {
    return "image";
  }

  if (
    media.includes("spreadsheet") ||
    media.includes("csv") ||
    media.includes("excel") ||
    filename.endsWith(".xlsx") ||
    filename.endsWith(".xls") ||
    filename.endsWith(".csv")
  ) {
    return "spreadsheet";
  }

  if (
    media.includes("markdown") ||
    media.includes("text/") ||
    media.includes("word") ||
    media.includes("pdf") ||
    media.includes("json") ||
    filename.endsWith(".md") ||
    filename.endsWith(".txt") ||
    filename.endsWith(".pdf") ||
    filename.endsWith(".docx") ||
    filename.endsWith(".doc") ||
    filename.endsWith(".json") ||
    filename.endsWith(".diff") ||
    filename.endsWith(".patch")
  ) {
    return "document";
  }

  return "other";
}

/**
 * Formats byte count to human readable units.
 */
export function formatArtifactSize(bytes: number): string {
  if (bytes <= 0 || Number.isNaN(bytes)) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const normalized = Math.min(i, units.length - 1);
  return `${(bytes / Math.pow(1024, normalized)).toFixed(normalized === 0 ? 0 : 1)} ${units[normalized]}`;
}

/**
 * Filters and searches artifact list.
 */
export function filterArtifacts(
  artifacts: LocalArtifact[],
  options: {
    query?: string;
    category?: ArtifactCategory;
    state?: "all" | "active" | "archived" | "revoked";
  } = {}
): LocalArtifact[] {
  const query = (options.query || "").trim().toLowerCase();
  const category = options.category || "all";
  const state = options.state || "all";

  return artifacts.filter((item) => {
    if (state !== "all" && item.lifecycle_state !== state) return false;
    if (category !== "all" && classifyArtifact(item) !== category) return false;

    if (!query) return true;
    const searchable = [
      item.filename,
      item.artifact_id,
      item.media_type,
      item.generation_method,
      item.lifecycle_state
    ]
      .join(" ")
      .toLowerCase();
    return searchable.includes(query);
  });
}
