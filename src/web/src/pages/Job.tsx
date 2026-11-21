import { ArrowLeft, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArtifactDownloadButton } from '@/components/ArtifactDownloadButton';
import { JobRenameDialog } from '@/components/JobRenameDialog';
import { NotaryDetailsCard } from '@/components/NotaryDetailsCard';
import {
  ErrorBox,
  Loading,
  PageHeading,
  StatusBadge,
} from '@/components/shared';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, api, errorMessage } from '@/lib/api';
import {
  date,
  type JobDetail,
  type JobRef,
  type NotaryDetails,
  size,
} from '@/lib/types';

export function JobPage() {
  const { jobId } = useParams();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [pollingPaused, setPollingPaused] = useState(false);
  const [notary, setNotary] = useState<NotaryDetails | null>(null);
  const [notaryError, setNotaryError] = useState('');
  const [notaryLoading, setNotaryLoading] = useState(false);
  const notaryCache = useRef<NotaryDetails | null>(null);
  const notaryPending = useRef(false);
  const running = useRef(false);
  const requests = useRef<AbortController | null>(null);
  const path = `/api/v1/jobs/${jobId}`;
  const fetchNotary = useCallback(
    async (value: JobDetail, controller: AbortController) => {
      const active = () =>
        requests.current === controller && !controller.signal.aborted;
      if (
        !active() ||
        !value.submission_id ||
        notaryCache.current ||
        notaryPending.current
      ) {
        return;
      }
      notaryPending.current = true;
      setNotaryLoading(true);
      try {
        const details = await api<NotaryDetails | null>(
          `${path}/notary-details`,
          {
            signal: controller.signal,
            timeoutMs: 120000,
          },
        );
        if (!active()) {
          return;
        }
        notaryCache.current = details;
        setNotary(details);
        setNotaryError('');
      } catch (error) {
        if (!active()) {
          return;
        }
        if (
          error instanceof ApiError &&
          [
            401,
            404,
          ].includes(error.status)
        ) {
          notaryCache.current = null;
          setNotary(null);
          setJob(null);
          setError(errorMessage(error));
        } else {
          setNotaryError(
            error instanceof ApiError && error.status === 409
              ? '签名详情正在获取或任务正在刷新，请稍后重试。'
              : errorMessage(error),
          );
        }
      } finally {
        if (active()) {
          notaryPending.current = false;
          setNotaryLoading(false);
        }
      }
    },
    [
      path,
    ],
  );
  useEffect(() => {
    const controller = new AbortController();
    requests.current = controller;
    running.current = false;
    setBusy(false);
    setRenaming(false);
    setLoading(true);
    setError('');
    setPollingPaused(false);
    setJob(null);
    notaryCache.current = null;
    notaryPending.current = false;
    setNotary(null);
    setNotaryError('');
    setNotaryLoading(false);
    api<JobDetail>(path, {
      signal: controller.signal,
    })
      .then(async (value) => {
        if (controller.signal.aborted) {
          return;
        }
        setJob(value);
        setLoading(false);
        await fetchNotary(value, controller);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });
    return () => {
      controller.abort();
      if (requests.current === controller) {
        requests.current = null;
      }
    };
  }, [
    path,
    fetchNotary,
  ]);
  const refresh = useCallback(async () => {
    const controller = requests.current;
    if (
      !controller ||
      controller.signal.aborted ||
      running.current ||
      notaryPending.current
    ) {
      return;
    }
    const active = () =>
      requests.current === controller && !controller.signal.aborted;
    const updateStatus = (value: JobRef, message = '') => {
      setJob((current) =>
        current?.id === value.id
          ? {
              ...current,
              ...value,
              error_message:
                value.status === 'failed'
                  ? message || current.error_message
                  : '',
            }
          : current,
      );
    };
    running.current = true;
    setBusy(true);
    setError('');
    setPollingPaused(false);
    try {
      try {
        const value = await api<JobRef>(`${path}/refresh`, {
          method: 'POST',
          signal: controller.signal,
          timeoutMs: 180000,
        });
        if (!active()) {
          return;
        }
        updateStatus(value);
      } catch (error) {
        if (!active()) {
          return;
        }
        const message =
          error instanceof ApiError && error.status === 409
            ? '该任务正在处理或刷新，请稍后再试。'
            : errorMessage(error);
        setError(message);
        setPollingPaused(true);
        if (error instanceof ApiError && error.data) {
          updateStatus(error.data, message);
        }
        if (
          error instanceof ApiError &&
          [
            401,
            404,
          ].includes(error.status)
        ) {
          setJob(null);
          return;
        }
        if (!(error instanceof ApiError) || error.status === 409) {
          return;
        }
      }
      if (!active()) {
        return;
      }
      try {
        const value = await api<JobDetail>(path, {
          signal: controller.signal,
        });
        if (active()) {
          setJob(value);
          await fetchNotary(value, controller);
        }
      } catch (error) {
        if (!active()) {
          return;
        }
        setError(errorMessage(error));
        setPollingPaused(true);
        if (
          error instanceof ApiError &&
          [
            401,
            404,
          ].includes(error.status)
        ) {
          setJob(null);
        }
      }
    } finally {
      if (active()) {
        running.current = false;
        setBusy(false);
        setRetry((value) => value + 1);
      }
    }
  }, [
    path,
    fetchNotary,
  ]);
  useEffect(() => {
    if (
      !job ||
      busy ||
      notaryLoading ||
      renaming ||
      pollingPaused ||
      ![
        'preparing',
        'processing',
      ].includes(job.status)
    ) {
      return;
    }
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      clearTimeout(timer);
      if (document.visibilityState === 'visible') {
        timer = setTimeout(() => void refresh(), 5000);
      }
    };
    schedule();
    document.addEventListener('visibilitychange', schedule);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', schedule);
    };
  }, [
    job,
    busy,
    notaryLoading,
    renaming,
    pollingPaused,
    refresh,
    retry,
  ]);
  return (
    <>
      <Link
        to="/jobs"
        className="mb-3 inline-flex items-center gap-1.5 text-[13px] text-muted-foreground hover:text-primary"
      >
        <ArrowLeft className="size-4" />
        返回签名历史
      </Link>
      <PageHeading
        title="任务详情"
        description="查看签名结果、公证状态与产物。"
        action={
          job && (
            <div className="flex flex-wrap gap-2">
              <JobRenameDialog
                key={job.id}
                job={job}
                disabled={busy || notaryLoading}
                onOpenChange={setRenaming}
                onRenamed={(value) =>
                  setJob((current) =>
                    current?.id === value.id
                      ? {
                          ...current,
                          display_name: value.display_name,
                          updated_at: value.updated_at,
                        }
                      : current,
                  )
                }
              />
              <Button
                variant="outline"
                disabled={renaming || notaryLoading}
                loading={busy}
                icon={RefreshCw}
                onClick={() => void refresh()}
              >
                刷新状态
              </Button>
              {job.status === 'completed' && (
                <ArtifactDownloadButton
                  key={job.id}
                  jobId={job.id}
                  onError={setError}
                />
              )}
            </div>
          )
        }
      />
      <ErrorBox message={error} />
      {loading ? (
        <Loading />
      ) : (
        job && (
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(260px,1fr)]">
            <Card>
              <CardHeader className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <CardTitle className="break-all text-base leading-6">
                    {job.display_name}
                  </CardTitle>
                  <p className="mt-1 break-all text-xs leading-5 text-muted-foreground">
                    {job.application.name} · {job.application.identifier}
                  </p>
                </div>
                <StatusBadge status={job.status} />
              </CardHeader>
              <CardContent>
                <dl className="grid gap-4 text-[13px] sm:grid-cols-2">
                  {[
                    [
                      '任务 ID',
                      job.id,
                    ],
                    [
                      '原始文件名',
                      job.filename,
                    ],
                    [
                      '文件大小',
                      size(job.size_bytes),
                    ],
                    [
                      '提交来源',
                      job.source,
                    ],
                    [
                      '创建时间',
                      date(job.created_at),
                    ],
                    [
                      '最后更新',
                      date(job.updated_at),
                    ],
                    [
                      '最近查询 Apple',
                      date(job.last_checked_at),
                    ],
                    [
                      'Apple Submission ID',
                      job.submission_id || '尚未提交',
                    ],
                  ].map(([name, value]) => (
                    <div key={name}>
                      <dt className="mb-1 text-xs text-muted-foreground">
                        {name}
                      </dt>
                      <dd className="break-all leading-5">{value}</dd>
                    </div>
                  ))}
                </dl>
                {[
                  'preparing',
                  'processing',
                ].includes(job.status) && (
                  <p className="mt-4 rounded-md bg-muted px-3 py-2.5 text-[13px] leading-5 text-muted-foreground">
                    正在等待签名或公证结果。此页面可见时每 5
                    秒刷新一次，请勿重复上传。
                  </p>
                )}
                <ErrorBox message={job.error_message} />
              </CardContent>
            </Card>
            <div className="min-w-0 space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>文件校验</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {[
                    [
                      '原始文件 SHA-256',
                      job.original_sha256,
                    ],
                    [
                      '签名产物 SHA-256',
                      job.artifact_sha256,
                    ],
                  ].map(([label, value]) => (
                    <div key={label}>
                      <p className="mb-1.5 text-xs text-muted-foreground">
                        {label}
                      </p>
                      <code className="block break-all rounded-md bg-muted p-2.5 text-xs leading-5">
                        {value || '尚未生成'}
                      </code>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </div>
            <NotaryDetailsCard
              key={job.id}
              details={notary}
              loading={notaryLoading}
              error={notaryError}
              disabled={busy || renaming}
              onRetry={() => {
                const controller = requests.current;
                if (controller && !running.current && !renaming) {
                  void fetchNotary(job, controller);
                }
              }}
            />
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>处理日志</CardTitle>
                <p className="text-xs text-muted-foreground">
                  签名与公证输出已由服务端脱敏。
                </p>
              </CardHeader>
              <CardContent>
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-md bg-slate-950 p-4 font-mono text-xs leading-5 text-slate-300">
                  {job.diagnostics || '暂无处理日志'}
                </pre>
              </CardContent>
            </Card>
          </div>
        )
      )}
    </>
  );
}
