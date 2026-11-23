DROP INDEX IF EXISTS idx_report_jobs_fingerprint_active;
CREATE UNIQUE INDEX idx_report_jobs_fingerprint_active
ON report_jobs(input_fingerprint)
WHERE status IN (
  'queued','resolving','checking_coverage','preparing_json','acquiring_materials',
  'building_handbook','reusing_handbook','validating_handbook','splitting_part_data',
  'context_preflight','generating_parts','summarizing','auditing','rewriting',
  'rendering','indexing','recoverable','cancelling'
);
