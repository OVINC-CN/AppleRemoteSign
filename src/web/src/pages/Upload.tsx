import { UploadCloud, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Empty, ErrorBox, PageHeading, selectClass } from '@/components/shared';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { ApiError, api, errorMessage, upload } from '@/lib/api';
import { useSession } from '@/lib/session';
import { type Application, size } from '@/lib/types';

export function UploadPage() {
  const { user } = useSession();
  const navigate = useNavigate();
  const [applications, setApplications] = useState<Application[]>([]);
  const [application, setApplication] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [jobId, setJobId] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    api<Application[]>('/api/v1/applications', {
      signal: controller.signal,
    })
      .then((apps) => {
        if (controller.signal.aborted) {
          return;
        }
        const enabled = apps.filter((app) => app.enabled);
        setApplications(enabled);
        if (enabled.length === 1) {
          setApplication(enabled[0].id);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoaded(true);
        }
      });
    return () => {
      controller.abort();
    };
  }, []);
  useEffect(() => {
    if (!busy) {
      return;
    }
    const guard = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [
    busy,
  ]);
  function choose(candidate?: File) {
    if (busy || !candidate) {
      return;
    }
    setError('');
    setJobId('');
    if (!candidate.name.toLowerCase().endsWith('.zip')) {
      setError('请选择包含一个完整 .app 或 .dmg 的 ZIP 文件。');
      return;
    }
    if (!/\d+\.\d+\.\d+/.test(candidate.name)) {
      setError('ZIP 文件名必须包含 X.Y.Z 版本号，例如 product-v1.2.3.zip。');
      return;
    }
    if (!candidate.size || candidate.size > (user?.max_upload_bytes || 0)) {
      setError(
        `文件大小必须介于 1 字节与 ${size(user?.max_upload_bytes || 0)} 之间。`,
      );
      return;
    }
    setFile(candidate);
  }
  async function submit() {
    if (!file || !application || busy) {
      return;
    }
    setBusy(true);
    setProgress(0);
    setError('');
    setJobId('');
    try {
      const result = await upload(application, file, setProgress);
      navigate(`/jobs/${result.id}`);
    } catch (error) {
      setError(errorMessage(error));
      if (error instanceof ApiError && error.data?.id) {
        setJobId(error.data.id);
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeading
        title="上传签名"
        description="选择应用并上传 App ZIP 或 DMG ZIP，发起签名与公证。"
      />
      <ErrorBox message={error} />
      {jobId && (
        <Button
          asChild
          variant="outline"
          className="mb-4"
        >
          <Link to={`/jobs/${jobId}`}>查看已创建任务</Link>
        </Button>
      )}
      {loaded && !applications.length ? (
        <Card>
          <Empty title="暂无可签名的应用">
            请联系管理员授权应用，或启用已有应用。
          </Empty>
        </Card>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(260px,1fr)]">
          <Card>
            <CardHeader>
              <CardTitle>准备签名文件</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="application">所属应用</Label>
                <select
                  id="application"
                  disabled={busy}
                  className={selectClass}
                  value={application}
                  onChange={(event) => setApplication(event.target.value)}
                >
                  <option value="">请选择应用</option>
                  {applications.map((app) => (
                    <option
                      key={app.id}
                      value={app.id}
                    >
                      {app.name} · {app.identifier}
                    </option>
                  ))}
                </select>
              </div>
              <input
                ref={input}
                aria-label="选择 ZIP 文件"
                type="file"
                accept=".zip,application/zip"
                className="sr-only"
                disabled={busy}
                onChange={(event) => {
                  choose(event.target.files?.[0]);
                  event.target.value = '';
                }}
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => input.current?.click()}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  choose(event.dataTransfer.files[0]);
                }}
                className={`flex min-h-44 w-full flex-col items-center justify-center rounded-lg border border-dashed p-5 text-center outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-wait ${dragging ? 'border-primary bg-accent' : 'border-input bg-muted/60 hover:border-primary/50'}`}
              >
                <span className="mb-3 flex size-9 items-center justify-center rounded-lg bg-secondary text-primary">
                  <UploadCloud
                    aria-hidden="true"
                    className="size-5"
                  />
                </span>
                <span className="text-[13px] font-medium">
                  拖拽 ZIP 到这里，或点击选择
                </span>
                <span className="mt-1.5 text-xs text-muted-foreground">
                  单个 App 或 DMG · 最大 {size(user?.max_upload_bytes || 0)}
                </span>
              </button>
              {file && (
                <div className="flex items-center gap-2 rounded-md border px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] font-medium">
                      {file.name}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {size(file.size)}
                    </p>
                  </div>
                  <Button
                    aria-label="移除文件"
                    variant="ghost"
                    size="icon-sm"
                    disabled={busy}
                    onClick={() => setFile(null)}
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              )}
              {busy && (
                <div role="status">
                  <div className="mb-2 flex justify-between text-[13px]">
                    <span>{progress < 100 ? '上传中…' : '签名并提交中…'}</span>
                    <span>{progress}%</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary transition-all"
                      style={{
                        width: `${progress}%`,
                      }}
                    />
                  </div>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">
                    上传完成后，服务仍需签名并提交
                    Apple。请保持页面打开；断开连接不会取消任务。
                  </p>
                </div>
              )}
              <Button
                className="w-full"
                disabled={!file || !application || busy}
                onClick={() => void submit()}
              >
                <UploadCloud className="size-4" />
                {busy ? '处理中，请稍候…' : '开始签名'}
              </Button>
              <p className="text-xs leading-5 text-muted-foreground">
                出现超时或连接错误时，请先到
                <Link
                  to="/jobs"
                  className="mx-1 text-primary underline"
                >
                  签名历史
                </Link>
                检查已有任务。
              </p>
            </CardContent>
          </Card>
          <Card className="h-fit bg-muted/40">
            <CardHeader>
              <CardTitle>上传前准备</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-[13px] leading-5 text-muted-foreground">
              <p>
                <span className="font-medium text-foreground">
                  01 · 打包完整应用
                </span>
                <br />
                ZIP 顶层应包含一个完整的 .app 目录，或一个 .dmg
                文件，不要再套一层文件夹。ZIP 文件名必须包含 X.Y.Z 版本号，可带
                v 前缀及任意预发布后缀。
              </p>
              <p>
                <span className="font-medium text-foreground">
                  02 · 保留文件结构
                </span>
                <br />在 macOS 上使用 ditto 打包，保留可执行权限和 Framework
                链接。
              </p>
              <code className="block overflow-x-auto rounded-md bg-muted p-2.5 text-xs leading-5">
                ditto -c -k --keepParent MyApp.app MyApp-v1.2.3.zip
              </code>
              <p>
                <span className="font-medium text-foreground">
                  03 · 等待公证结果
                </span>
                <br />
                提交后会进入任务详情。Apple 公证通过后生成可下载的签名产物。
              </p>
              <p className="border-t pt-3 text-xs leading-5">
                DMG
                仅签名外层文件，内部应用应预先完成代码签名。同应用的同内容、同文件名会复用有效任务。
              </p>
            </CardContent>
          </Card>
        </div>
      )}
    </>
  );
}
