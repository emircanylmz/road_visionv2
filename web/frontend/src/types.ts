// API sözleşmesi tipleri (WEB_PLANI.md §6).

export interface UserInfo {
  user_id: number;
  email: string;
  full_name: string;
  role: "member" | "admin";
  status: "pending" | "approved" | "rejected" | "disabled";
  created_at: string;
  approved_at: string | null;
}

export interface MeResponse {
  user: UserInfo;
  session_expires_at: string;
}

export type LogLevel = "debug" | "info" | "warning" | "error";
export type LogCategory = "app" | "detection";

export interface LogRecordRow {
  id: number;
  ts: string;
  level: LogLevel;
  category: LogCategory;
  message: string;
  run_id: number | null;
  model_id: string | null;
  has_payload: boolean;
}

export interface LogPage {
  records: LogRecordRow[];
  next_cursor: string | null;
}

export interface LogDetail {
  record: Omit<LogRecordRow, "has_payload"> & {
    payload: Record<string, unknown>;
  };
}

export interface ModelInfo {
  model_id: string;
  display_name: string;
  task: string;
  input_size: number;
  active: boolean;
}

export interface SessionInfo {
  session_id: string;
  user_id: number;
  email: string;
  created_at: string;
  expires_at: string;
  last_seen_at: string;
  ip: string | null;
  user_agent: string | null;
}

export interface AuditEntry {
  audit_id: number;
  actor_id: number;
  actor_email: string;
  action: string;
  target: string;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export type ReviewStatus = "unreviewed" | "correct" | "corrected" | "wrong";

export interface TypeCounts {
  total: number;
  unreviewed: number;
  correct: number;
  corrected: number;
  wrong: number;
}

export interface ArchiveTypeInfo {
  type_id: number;
  class_name: string;
  display_name: string;
  is_catalogued: boolean;
  counts: TypeCounts;
}

export interface ArchiveModelNode {
  model_id: string;
  display_name: string;
  task: string | null;
  active: boolean | null;
  types: ArchiveTypeInfo[];
}

export interface DetectionRow {
  id: number;
  ts: string;
  run_id: number | null;
  model_id: string;
  model_display_name: string;
  type_id: number;
  class_name: string;
  type_display_name: string;
  is_catalogued: boolean;
  confidence: number | null;
  area_ratio: number | null;
  bbox: number[] | null;
  capture_id: string | null;
  original_media_id: number | null;
  annotated_media_id: number | null;
  review_status: ReviewStatus;
  reviewed_at: string | null;
}

export interface DetectionPage {
  records: DetectionRow[];
  next_cursor: string | null;
}

export interface CaptureDetail {
  capture: {
    capture_id: string;
    ts: string;
    run_id: number | null;
    source_name: string | null;
    source_kind: string | null;
    frame_sequence: number | null;
    is_reprocess: boolean;
    original: MediaBlobInfo;
    annotated: MediaBlobInfo;
    models: { model_id: string; object_count: number }[];
  };
}

export interface MediaBlobInfo {
  media_id: number;
  mime: string;
  width: number;
  height: number;
  byte_size: number;
}
