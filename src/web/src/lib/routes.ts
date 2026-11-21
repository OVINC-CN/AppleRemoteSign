export const pagePaths = {
  home: '/',
  login: '/login',
  jobs: '/jobs',
  jobDetail: '/jobs/detail',
  upload: '/upload',
  adminApplications: '/admin/applications',
} as const;

export function jobDetailUrl(jobId: string) {
  const query = new URLSearchParams({
    job_id: jobId,
  });
  return `${pagePaths.jobDetail}?${query}`;
}
