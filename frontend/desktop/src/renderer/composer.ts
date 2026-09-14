/** Pure Composer parsing helpers; stable IDs stay outside visible prompt text. */
export function activeSlashQuery(value: string): string | null {
  const match = value.match(/(?:^|\s)\/([^\s/]*)$/);
  return match ? (match[1] ?? "") : null;
}

export function replaceSlashToken(value: string, commandId: string): string {
  const normalized = commandId.trim().replace(/[^a-z0-9_-]/gi, "").slice(0, 64);
  return value.replace(
    /(^|\s)\/[^\s/]*$/,
    (_match, prefix: string) => `${prefix}/${normalized} `
  );
}

export function serializeResourceToken(referenceId: string): string {
  return `resource:${referenceId.trim()}`;
}

export function removeResourceToken(referenceIds: string[], referenceId: string): string[] {
  return referenceIds.filter((item) => item !== referenceId);
}
