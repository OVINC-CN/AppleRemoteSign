export interface Application {
  id: string;
  identifier: string;
  name: string;
  enabled: boolean;
  created_at: string;
}
export interface Me {
  username: string;
  is_admin: boolean;
  csrf_token: string;
  max_upload_bytes: number;
}
export type JobStatus = 'preparing' | 'processing' | 'completed' | 'failed';
export interface JobRef {
  id: string;
  status: JobStatus;
  submission_id: string | null;
}
export interface Job extends JobRef {
  application: Application;
  filename: string;
  display_name: string;
  source: string;
  size_bytes: number | null;
  error_message: string;
  created_at: string;
  updated_at: string;
  last_checked_at: string | null;
}
export interface JobDetail extends Job {
  diagnostics: string;
  notary_log: string | null;
  original_sha256: string | null;
  artifact_sha256: string | null;
}
export interface NotaryTicket {
  path: string;
  arch: string | null;
  cdhash: string;
  digestAlgorithm: string;
}
export interface NotaryIssue {
  severity: string | null;
  code: number | string | null;
  path: string | null;
  architecture: string | null;
  message: string | null;
  docUrl: string | null;
}
export interface NotaryDetails {
  archiveFilename: string;
  jobId: string;
  logFormatVersion: number;
  sha256: string;
  status: 'Accepted';
  statusCode: number;
  statusSummary: string;
  uploadDate: string;
  issues: NotaryIssue[] | null;
  ticketContents: NotaryTicket[] | null;
}
export interface JobPage {
  items: Job[];
  total: number;
  page: number;
  page_size: number;
}
export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}
export const statusLabels: Record<JobStatus, string> = {
  preparing: '准备中',
  processing: '处理中',
  completed: '已完成',
  failed: '失败',
};
export function date(value: string | null) {
  return value
    ? new Date(value).toLocaleString('zh-CN', {
        hour12: false,
      })
    : '—';
}
export function duration(start: string, end: string | number) {
  const elapsed = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(elapsed)) {
    return '—';
  }
  const seconds = Math.max(0, Math.floor(elapsed / 1000));
  return [
    Math.floor(seconds / 3600),
    Math.floor((seconds % 3600) / 60),
    seconds % 60,
  ]
    .map((value) => String(value).padStart(2, '0'))
    .join(':');
}
export function size(value: number | null) {
  if (value === null) {
    return '—';
  }
  return value < 1024 ** 2
    ? `${(value / 1024).toFixed(1)} KB`
    : `${(value / 1024 ** 2).toFixed(1)} MB`;
}
