import { ArrowRight, Plus, RefreshCw, Search } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArtifactDownloadButton } from '@/components/ArtifactDownloadButton';
import {
  Empty,
  ErrorBox,
  Loading,
  PageHeading,
  StatusBadge,
} from '@/components/shared';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tooltip } from '@/components/ui/tooltip';
import { api, errorMessage } from '@/lib/api';
import { jobDetailUrl, pagePaths } from '@/lib/routes';
import {
  type Application,
  date,
  duration,
  type JobPage as PageData,
  size,
  statusLabels,
} from '@/lib/types';

export function HistoryPage() {
  const [params, setParams] = useSearchParams();
  const [applications, setApplications] = useState<Application[]>([]);
  const [data, setData] = useState<PageData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [now, setNow] = useState(Date.now);
  const [search, setSearch] = useState(params.get('filename') || '');
  const loadedQuery = useRef('');
  const page = Math.max(1, Number(params.get('page')) || 1);
  const hasRunningJobs = data?.items.some(
    (job) => job.status === 'preparing' || job.status === 'processing',
  );
  useEffect(() => {
    if (!hasRunningJobs) {
      return;
    }
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [
    hasRunningJobs,
  ]);
  useEffect(() => {
    const controller = new AbortController();
    api<Application[]>('/api/v1/applications', {
      signal: controller.signal,
    })
      .then((apps) => {
        if (!controller.signal.aborted) {
          setApplications(apps);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(error));
        }
      });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    const query = new URLSearchParams(params);
    query.set('page', String(page));
    query.set('page_size', '20');
    const queryKey = query.toString();
    const retainData = loadedQuery.current === queryKey;
    if (!retainData) {
      setData(null);
    }
    api<PageData>(`/api/v1/jobs?${query}`, {
      signal: controller.signal,
    })
      .then((data) => {
        if (!controller.signal.aborted) {
          loadedQuery.current = queryKey;
          setData(data);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          if (!retainData) {
            setData(null);
          }
          setError(errorMessage(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [
    params,
    page,
    revision,
  ]);
  function filter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    next.delete('page');
    setParams(next);
  }
  return (
    <>
      <PageHeading
        title="签名历史"
        description="查看你有权限的应用的签名与公证记录。"
        action={
          <Button asChild>
            <Link to={pagePaths.upload}>
              <Plus className="size-4" />
              上传签名
            </Link>
          </Button>
        }
      />
      <Card
        role="region"
        aria-label="签名历史列表"
        aria-busy={loading}
        className="gap-0 overflow-hidden py-0"
      >
        <div className="grid grid-cols-2 gap-2 border-b p-3 lg:grid-cols-[minmax(0,1fr)_144px_128px_auto]">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              filter('filename', search);
            }}
            className="col-span-2 flex min-w-0 gap-2 lg:col-span-1 lg:max-w-lg"
          >
            <div className="relative flex-1">
              <Search
                aria-hidden="true"
                className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                aria-label="搜索任务名或文件名"
                className="pl-8"
                placeholder="搜索任务名或文件名…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <Button
              variant="outline"
              type="submit"
              loading={loading}
            >
              搜索
            </Button>
          </form>
          <Select
            aria-label="筛选应用"
            value={params.get('application_id') || ''}
            onValueChange={(value) => filter('application_id', value)}
            options={[
              {
                value: '',
                label: '所有可见应用',
              },
              ...applications.map((app) => ({
                value: app.id,
                label: app.name,
              })),
            ]}
          />
          <Select
            aria-label="筛选状态"
            value={params.get('status') || ''}
            onValueChange={(value) => filter('status', value)}
            options={[
              {
                value: '',
                label: '全部状态',
              },
              ...Object.entries(statusLabels).map(([value, label]) => ({
                value,
                label,
              })),
            ]}
          />
          <Tooltip text="刷新当前筛选下的签名历史">
            <Button
              variant="outline"
              loading={loading}
              icon={RefreshCw}
              iconClassName="size-3.5"
              aria-label="刷新签名历史"
              className="col-span-2 min-w-20 justify-self-end lg:col-span-1"
              onClick={() => {
                if (loading) {
                  return;
                }
                setLoading(true);
                setRevision((value) => value + 1);
              }}
            >
              刷新
            </Button>
          </Tooltip>
        </div>
        <div className="px-4">
          <ErrorBox message={error} />
        </div>
        {loading && !data ? (
          <Loading />
        ) : !data?.items.length ? (
          <Empty title={error ? '无法加载签名历史' : '还没有签名记录'}>
            {error
              ? '请检查连接后重新打开此页面。'
              : applications.length
                ? '上传一个 ZIP 开始签名，或调整筛选条件。'
                : '当前没有获授权的应用，请联系管理员。'}
          </Empty>
        ) : (
          <Table className="min-w-[940px] table-fixed text-left">
            <colgroup>
              <col />
              <col className="w-[104px]" />
              <col className="w-[176px]" />
              <col className="w-[128px]" />
              <col className="w-[112px]" />
              <col className="w-[128px]" />
            </colgroup>
            <TableHeader className="bg-muted/70">
              <TableRow className="hover:bg-transparent">
                {[
                  '任务 / 应用',
                  '状态',
                  '提交来源',
                  '创建时间',
                  '耗时',
                  '操作',
                ].map((label, index) => (
                  <TableHead
                    key={label}
                    scope="col"
                    className={index === 5 ? 'text-right' : undefined}
                  >
                    {label}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((job) => (
                <TableRow
                  key={job.id}
                  className="focus-within:bg-muted/50"
                >
                  <TableCell>
                    <Tooltip text={job.display_name}>
                      <Link
                        to={jobDetailUrl(job.id)}
                        className="block truncate font-medium hover:text-primary focus-visible:underline"
                      >
                        {job.display_name}
                      </Link>
                    </Tooltip>
                    <Tooltip
                      text={`${job.filename} · ${job.application.name} · ${size(job.size_bytes)}`}
                    >
                      <div className="mt-0.5 truncate text-xs leading-4 text-muted-foreground">
                        {job.display_name !== job.filename && (
                          <>{job.filename} · </>
                        )}
                        {job.application.name} · {size(job.size_bytes)}
                      </div>
                    </Tooltip>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    <StatusBadge status={job.status} />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    <Tooltip text={job.source}>
                      <span className="block truncate">{job.source}</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs tabular-nums text-muted-foreground">
                    <Tooltip text={date(job.created_at)}>
                      <time dateTime={job.created_at}>
                        {new Date(job.created_at)
                          .toLocaleString('zh-CN', {
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit',
                            hour12: false,
                          })
                          .replaceAll('/', '-')}
                      </time>
                    </Tooltip>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs tabular-nums text-muted-foreground">
                    <Tooltip text="时:分:秒">
                      <span>
                        {duration(
                          job.created_at,
                          job.status === 'completed' || job.status === 'failed'
                            ? job.updated_at
                            : now,
                        )}
                      </span>
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-1">
                      {job.status === 'completed' && (
                        <ArtifactDownloadButton
                          jobId={job.id}
                          label={`下载 ${job.display_name}`}
                          compact
                          onError={setError}
                        />
                      )}
                      <Button
                        asChild
                        variant="ghost"
                        size="sm"
                      >
                        <Link to={jobDetailUrl(job.id)}>
                          详情
                          <ArrowRight
                            aria-hidden="true"
                            className="size-3.5"
                          />
                        </Link>
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t px-4 py-2 text-xs text-muted-foreground">
          <span>共 {data?.total ?? 0} 条记录</span>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={loading || page <= 1}
              onClick={() => {
                const next = new URLSearchParams(params);
                next.set('page', String(page - 1));
                setParams(next);
              }}
            >
              上一页
            </Button>
            <span className="tabular-nums">第 {page} 页</span>
            <Button
              size="sm"
              variant="outline"
              disabled={loading || !data || page * 20 >= data.total}
              onClick={() => {
                const next = new URLSearchParams(params);
                next.set('page', String(page + 1));
                setParams(next);
              }}
            >
              下一页
            </Button>
          </div>
        </div>
      </Card>
    </>
  );
}
