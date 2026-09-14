export type DiffLineType = "add" | "delete" | "context" | "header";

export type DiffLine = {
  type: DiffLineType;
  oldLineNumber?: number;
  newLineNumber?: number;
  content: string;
};

export type DiffChunk = {
  header: string;
  lines: DiffLine[];
};

export type ParsedDiff = {
  oldPath?: string;
  newPath?: string;
  filePath: string;
  additions: number;
  deletions: number;
  chunks: DiffChunk[];
};

/**
 * Parses a standard Unified Diff text string into structured chunks and line counts.
 */
export function parseUnifiedDiff(rawDiff: string, fallbackPath = "unknown-file"): ParsedDiff {
  if (!rawDiff || typeof rawDiff !== "string") {
    return {
      filePath: fallbackPath,
      additions: 0,
      deletions: 0,
      chunks: []
    };
  }

  const lines = rawDiff.split("\n");
  let oldPath: string | undefined;
  let newPath: string | undefined;
  let additions = 0;
  let deletions = 0;
  const chunks: DiffChunk[] = [];
  let currentChunk: DiffChunk | null = null;

  let oldLineCounter = 0;
  let newLineCounter = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (line.startsWith("--- ")) {
      oldPath = line.substring(4).replace(/^[ab]\//, "").trim();
      continue;
    }
    if (line.startsWith("+++ ")) {
      newPath = line.substring(4).replace(/^[ab]\//, "").trim();
      continue;
    }
    if (line.startsWith("diff --git")) {
      continue;
    }
    if (line.startsWith("index ")) {
      continue;
    }

    if (line.startsWith("@@")) {
      const match = /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/.exec(line);
      if (match) {
        oldLineCounter = parseInt(match[1], 10);
        newLineCounter = parseInt(match[2], 10);
      }
      currentChunk = {
        header: line,
        lines: []
      };
      chunks.push(currentChunk);
      continue;
    }

    if (!currentChunk) {
      currentChunk = {
        header: "@@ -1 +1 @@",
        lines: []
      };
      chunks.push(currentChunk);
    }

    if (line.startsWith("+")) {
      additions++;
      currentChunk.lines.push({
        type: "add",
        newLineNumber: newLineCounter++,
        content: line.substring(1)
      });
    } else if (line.startsWith("-")) {
      deletions++;
      currentChunk.lines.push({
        type: "delete",
        oldLineNumber: oldLineCounter++,
        content: line.substring(1)
      });
    } else if (line.startsWith(" ")) {
      currentChunk.lines.push({
        type: "context",
        oldLineNumber: oldLineCounter++,
        newLineNumber: newLineCounter++,
        content: line.substring(1)
      });
    } else if (line.length > 0) {
      // Lines without standard prefix, treat as context
      currentChunk.lines.push({
        type: "context",
        oldLineNumber: oldLineCounter++,
        newLineNumber: newLineCounter++,
        content: line
      });
    }
  }

  const derivedPath = newPath || oldPath || fallbackPath;

  return {
    oldPath,
    newPath,
    filePath: derivedPath,
    additions,
    deletions,
    chunks
  };
}
