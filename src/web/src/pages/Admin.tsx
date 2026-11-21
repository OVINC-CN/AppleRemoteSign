import {
  AppWindow,
  Check,
  Copy,
  KeyRound,
  Plus,
  ShieldCheck,
  Users,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Empty, ErrorBox, Loading, PageHeading } from '@/components/shared';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { api, errorMessage } from '@/lib/api';
import { type ApiKey, type Application, date } from '@/lib/types';

type Operation =
  | 'create-app'
  | 'update-app'
  | 'save-grants'
  | 'create-key'
  | 'revoke-key';
const identifierFormat = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export function AdminPage() {
  const [applications, setApplications] = useState<Application[]>([]);
  const [selected, setSelected] = useState('');
  const [users, setUsers] = useState<string[]>([]);
  const [grants, setGrants] = useState<string[]>([]);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [operation, setOperation] = useState<Operation | null>(null);
  const busy = operation !== null;
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [identifier, setIdentifier] = useState('');
  const [identifierInvalid, setIdentifierInvalid] = useState(false);
  const [editName, setEditName] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [keyName, setKeyName] = useState('');
  const [secret, setSecret] = useState('');
  const [copied, setCopied] = useState(false);
  const [copying, setCopying] = useState(false);
  const [copyError, setCopyError] = useState('');
  const [revokeKeyId, setRevokeKeyId] = useState('');
  const app = applications.find((item) => item.id === selected);
  const prefix = `/api/v1/admin/applications/${selected}`;
  const loadApps = useCallback(async (signal?: AbortSignal) => {
    const apps = await api<Application[]>('/api/v1/admin/applications', {
      signal,
    });
    if (signal?.aborted) {
      return;
    }
    setApplications(apps);
    setSelected((value) => value || apps[0]?.id || '');
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      loadApps(controller.signal),
      api<string[]>('/api/v1/admin/users', {
        signal: controller.signal,
      }).then((users) => {
        if (!controller.signal.aborted) {
          setUsers(users);
        }
      }),
    ])
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
    return () => controller.abort();
  }, [
    loadApps,
  ]);
  useEffect(() => {
    if (!selected) {
      return;
    }
    const controller = new AbortController();
    setDetailsLoading(true);
    setError('');
    setSuccess('');
    setSecret('');
    setKeys([]);
    setGrants([]);
    Promise.all([
      api<ApiKey[]>(`${prefix}/keys`, {
        signal: controller.signal,
      }),
      api<string[]>(`${prefix}/users`, {
        signal: controller.signal,
      }),
    ])
      .then(([keys, grants]) => {
        if (!controller.signal.aborted) {
          setKeys(keys);
          setGrants(grants);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setDetailsLoading(false);
        }
      });
    return () => {
      controller.abort();
    };
  }, [
    selected,
    prefix,
  ]);
  useEffect(() => {
    setEditName(app?.name || '');
    setEnabled(app?.enabled ?? true);
  }, [
    app,
  ]);
  async function act(kind: Operation, run: () => Promise<void>, message = '') {
    if (busy) {
      return;
    }
    setOperation(kind);
    setError('');
    setSuccess('');
    try {
      await run();
      setSuccess(message);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setOperation(null);
    }
  }
  function createApplication() {
    if (!name.trim() || !identifier.trim()) {
      return;
    }
    if (!identifierFormat.test(identifier)) {
      setIdentifierInvalid(true);
      return;
    }
    return act(
      'create-app',
      async () => {
        const created = await api<Application>('/api/v1/admin/applications', {
          method: 'POST',
          body: JSON.stringify({
            name: name.trim(),
            identifier: identifier.trim(),
          }),
        });
        await loadApps();
        setSelected(created.id);
        setCreateOpen(false);
        setName('');
        setIdentifier('');
      },
      '应用已创建。',
    );
  }
  function createKey() {
    if (!keyName.trim()) {
      return;
    }
    return act('create-key', async () => {
      const result = await api<{
        key: ApiKey;
        token: string;
      }>(`${prefix}/keys`, {
        method: 'POST',
        body: JSON.stringify({
          name: keyName.trim(),
        }),
      });
      setKeys((value) => [
        result.key,
        ...value,
      ]);
      setSecret(result.token);
      setCopied(false);
      setCopyError('');
      setKeyName('');
    });
  }
  async function copyKey() {
    setCopying(true);
    setCopied(false);
    setCopyError('');
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
    } catch {
      setCopyError('复制失败，请手动选中并保存 Key。');
    } finally {
      setCopying(false);
    }
  }
  return (
    <>
      <PageHeading
        title="应用与权限"
        description="按应用分配访问权限，为自动化流程签发独立的 API Key。"
        action={
          <Dialog
            open={createOpen}
            onOpenChange={(open) => {
              if (!busy) {
                setCreateOpen(open);
                setIdentifierInvalid(false);
              }
            }}
          >
            <DialogTrigger asChild>
              <Button disabled={busy}>
                <Plus className="size-4" />
                新建应用
              </Button>
            </DialogTrigger>
            <DialogContent showCloseButton={!busy}>
              <DialogHeader>
                <DialogTitle>新建应用</DialogTitle>
                <DialogDescription>
                  identifier 是应用的固定标识，创建后不可修改。
                </DialogDescription>
              </DialogHeader>
              <form
                noValidate
                className="space-y-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void createApplication();
                }}
              >
                <div className="space-y-2">
                  <Label htmlFor="new-name">应用名称</Label>
                  <Input
                    id="new-name"
                    required
                    maxLength={255}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="例如：团队桌面客户端"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="new-identifier">Identifier</Label>
                  <Input
                    id="new-identifier"
                    required
                    maxLength={128}
                    aria-invalid={identifierInvalid || undefined}
                    aria-describedby={
                      identifierInvalid ? 'new-identifier-error' : undefined
                    }
                    value={identifier}
                    onChange={(event) => {
                      setIdentifier(event.target.value);
                      setIdentifierInvalid(false);
                    }}
                    placeholder="com.example.desktop"
                  />
                  {identifierInvalid && (
                    <p
                      id="new-identifier-error"
                      role="alert"
                      className="text-xs text-destructive"
                    >
                      Identifier
                      必须以字母或数字开头，仅可包含字母、数字、点、下划线和连字符。
                    </p>
                  )}
                </div>
                <ErrorBox message={error} />
                <Button
                  type="submit"
                  disabled={busy || !name.trim() || !identifier.trim()}
                  loading={operation === 'create-app'}
                  className="w-full"
                >
                  创建应用
                </Button>
              </form>
            </DialogContent>
          </Dialog>
        }
      />
      <ErrorBox message={error} />
      {success && (
        <p
          role="status"
          className="mb-4 flex items-center gap-2 text-[13px] text-emerald-700"
        >
          <Check className="size-4" />
          {success}
        </p>
      )}
      {loading ? (
        <Loading />
      ) : !applications.length ? (
        <Card>
          <Empty title="创建第一个应用">
            为应用设置 identifier，再添加可访问用户或签发 API Key。
          </Empty>
        </Card>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[208px_minmax(0,1fr)]">
          <Card className="gap-0 py-0">
            <nav
              aria-label="应用列表"
              className="space-y-1 p-2"
            >
              {applications.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  disabled={busy}
                  aria-pressed={selected === item.id}
                  onClick={() => setSelected(item.id)}
                  className={`flex w-full gap-2 rounded-md px-2.5 py-2.5 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30 ${selected === item.id ? 'bg-accent text-primary' : 'hover:bg-muted'}`}
                >
                  <AppWindow className="mt-0.5 size-4 shrink-0" />
                  <span className="min-w-0">
                    <span className="block truncate text-[13px] font-medium leading-5">
                      {item.name}
                      {!item.enabled && (
                        <span className="ml-2 text-xs text-muted-foreground">
                          已停用
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                      {item.identifier}
                    </span>
                  </span>
                </button>
              ))}
            </nav>
          </Card>
          <div className="min-w-0 space-y-4">
            {app && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <ShieldCheck className="size-4 text-primary" />
                    应用设置
                  </CardTitle>
                  <p className="break-all font-mono text-xs text-muted-foreground">
                    {app.identifier}
                  </p>
                </CardHeader>
                <CardContent>
                  <form
                    noValidate
                    className="flex flex-wrap items-end gap-3"
                    onSubmit={(event) => {
                      event.preventDefault();
                      if (!editName.trim()) {
                        return;
                      }
                      void act(
                        'update-app',
                        async () => {
                          await api(prefix, {
                            method: 'PATCH',
                            body: JSON.stringify({
                              name: editName.trim(),
                              enabled,
                            }),
                          });
                          await loadApps();
                        },
                        '应用设置已保存。',
                      );
                    }}
                  >
                    <div className="min-w-0 flex-[1_1_192px] space-y-1.5">
                      <Label htmlFor="edit-name">显示名称</Label>
                      <Input
                        id="edit-name"
                        value={editName}
                        maxLength={255}
                        required
                        onChange={(event) => setEditName(event.target.value)}
                      />
                    </div>
                    <label
                      htmlFor="application-enabled"
                      className="flex h-8 items-center gap-2 text-[13px]"
                    >
                      <Checkbox
                        id="application-enabled"
                        checked={enabled}
                        onCheckedChange={(value) => setEnabled(value === true)}
                        disabled={busy}
                      />
                      启用应用
                    </label>
                    <Button
                      type="submit"
                      variant="outline"
                      disabled={busy || !editName.trim()}
                      loading={operation === 'update-app'}
                    >
                      保存应用
                    </Button>
                  </form>
                  <p className="mt-3 text-xs text-muted-foreground">
                    停用后，普通用户与 API Key 无法访问；管理员仍可查看历史。
                  </p>
                </CardContent>
              </Card>
            )}
            {detailsLoading ? (
              <Loading />
            ) : (
              <>
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Users className="size-4 text-primary" />
                      可访问用户
                    </CardTitle>
                    <p className="text-xs text-muted-foreground">
                      授权用户可查看全部历史、下载产物并上传签名。管理员默认拥有访问权限。
                    </p>
                  </CardHeader>
                  <CardContent>
                    <div className="mb-4 flex flex-wrap gap-2">
                      {users.map((username) => (
                        <label
                          key={username}
                          htmlFor={`user-grant-${encodeURIComponent(username)}`}
                          className={`flex min-h-8 cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-[13px] ${grants.includes(username) ? 'border-primary/20 bg-muted' : ''}`}
                        >
                          <Checkbox
                            id={`user-grant-${encodeURIComponent(username)}`}
                            checked={grants.includes(username)}
                            disabled={busy}
                            onCheckedChange={(value) =>
                              setGrants((current) =>
                                value
                                  ? [
                                      ...current,
                                      username,
                                    ]
                                  : current.filter((item) => item !== username),
                              )
                            }
                          />
                          {username}
                        </label>
                      ))}
                    </div>
                    <Button
                      disabled={busy}
                      loading={operation === 'save-grants'}
                      onClick={() =>
                        void act(
                          'save-grants',
                          async () => {
                            await api(`${prefix}/users`, {
                              method: 'PUT',
                              body: JSON.stringify({
                                usernames: grants,
                              }),
                            });
                          },
                          '用户授权已保存，即刻生效。',
                        )
                      }
                    >
                      保存授权
                    </Button>
                    <p className="mt-3 text-xs text-muted-foreground">
                      名单来自部署配置。可在用户首次登录前完成授权。
                    </p>
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <KeyRound className="size-4 text-primary" />
                      OpenAPI Keys
                    </CardTitle>
                    <p className="text-xs text-muted-foreground">
                      每个 Key 仅访问当前应用。完整 Key 仅在创建时显示一次。
                    </p>
                  </CardHeader>
                  <CardContent>
                    <form
                      noValidate
                      className="mb-4 flex flex-wrap gap-2 sm:flex-nowrap"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void createKey();
                      }}
                    >
                      <Input
                        aria-label="Key 名称"
                        required
                        maxLength={255}
                        value={keyName}
                        onChange={(event) => setKeyName(event.target.value)}
                        placeholder="Key 名称，例如 GitHub Actions"
                      />
                      <Button
                        type="submit"
                        disabled={busy || !keyName.trim()}
                        loading={operation === 'create-key'}
                        icon={Plus}
                      >
                        创建 Key
                      </Button>
                    </form>
                    {!keys.length ? (
                      <Empty title="尚未签发 Key">
                        网页签名无需 API Key，自动化调用时再创建。
                      </Empty>
                    ) : (
                      <div className="divide-y">
                        {keys.map((key) => (
                          <div
                            key={key.id}
                            className="flex flex-wrap items-center justify-between gap-3 py-3"
                          >
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="break-all text-[13px] font-medium">
                                  {key.name}
                                </span>
                                <Badge
                                  variant={
                                    key.revoked_at ? 'secondary' : 'outline'
                                  }
                                >
                                  {key.revoked_at ? '已吊销' : '有效'}
                                </Badge>
                              </div>
                              <code className="mt-1 block text-xs text-muted-foreground">
                                {key.prefix}••••••••
                              </code>
                              <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                                创建于 {date(key.created_at)} · 最近使用{' '}
                                {date(key.last_used_at)}
                              </p>
                            </div>
                            {!key.revoked_at && (
                              <AlertDialog
                                open={revokeKeyId === key.id}
                                onOpenChange={(open) => {
                                  if (!busy) {
                                    setError('');
                                    setRevokeKeyId(open ? key.id : '');
                                  }
                                }}
                              >
                                <AlertDialogTrigger asChild>
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={busy}
                                  >
                                    吊销
                                  </Button>
                                </AlertDialogTrigger>
                                <AlertDialogContent>
                                  <AlertDialogHeader>
                                    <AlertDialogTitle>
                                      吊销 {key.name}？
                                    </AlertDialogTitle>
                                    <AlertDialogDescription>
                                      使用此 Key
                                      的后续请求将立即被拒绝。此操作无法撤销，需要时可创建新的
                                      Key。
                                    </AlertDialogDescription>
                                  </AlertDialogHeader>
                                  <ErrorBox message={error} />
                                  <AlertDialogFooter>
                                    <AlertDialogCancel disabled={busy}>
                                      取消
                                    </AlertDialogCancel>
                                    <AlertDialogAction
                                      disabled={busy}
                                      aria-busy={
                                        operation === 'revoke-key' || undefined
                                      }
                                      onClick={(event) => {
                                        event.preventDefault();
                                        void act(
                                          'revoke-key',
                                          async () => {
                                            await api(
                                              `${prefix}/keys/${key.id}/revoke`,
                                              {
                                                method: 'POST',
                                              },
                                            );
                                            setKeys(
                                              await api<ApiKey[]>(
                                                `${prefix}/keys`,
                                              ),
                                            );
                                            setRevokeKeyId('');
                                          },
                                          'Key 已吊销。',
                                        );
                                      }}
                                    >
                                      确认吊销
                                    </AlertDialogAction>
                                  </AlertDialogFooter>
                                </AlertDialogContent>
                              </AlertDialog>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </>
            )}
          </div>
        </div>
      )}
      <Dialog
        open={!!secret}
        onOpenChange={(open) => {
          if (!open && !copying) {
            setSecret('');
          }
        }}
      >
        <DialogContent showCloseButton={!copying}>
          <DialogHeader>
            <DialogTitle>保存你的 API Key</DialogTitle>
            <DialogDescription>
              这是完整 Key 唯一一次显示。请保存到 CI
              的密钥存储中，关闭后无法再次查看。
            </DialogDescription>
          </DialogHeader>
          <code
            data-testid="created-key"
            className="block break-all rounded-md bg-slate-950 p-3 text-[13px] leading-5 text-slate-100"
          >
            {secret}
          </code>
          <div className="flex gap-2">
            <Button
              variant="outline"
              loading={copying}
              icon={copied ? Check : Copy}
              onClick={() => void copyKey()}
            >
              复制 Key
            </Button>
            <Button
              className="flex-1"
              disabled={copying}
              onClick={() => setSecret('')}
            >
              已保存，关闭
            </Button>
          </div>
          <ErrorBox message={copyError} />
          {copied && (
            <p
              role="status"
              className="text-xs text-emerald-700"
            >
              Key 已复制
            </p>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
