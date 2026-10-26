export interface Finding { path: string; line: number; severity: string; message: string; term?: string }
export function scanSource(path: string, source: string): Finding[];
export function sourceFiles(directory: string): string[];
