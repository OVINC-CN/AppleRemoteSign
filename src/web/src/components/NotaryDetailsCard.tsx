import { FileCode, Folder, Package, RefreshCw } from 'lucide-react';
import { useMemo, useState } from 'react';
import { ErrorBox, Loading } from '@/components/shared';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { buildNotaryTopology, nodeHeight, nodeWidth } from '@/lib/notary';
import { date, type NotaryDetails } from '@/lib/types';

export function NotaryDetailsCard({
  details,
  loading,
  error,
  disabled,
  onRetry,
}: {
  details: NotaryDetails | null;
  loading: boolean;
  error: string;
  disabled: boolean;
  onRetry: () => void;
}) {
  if (!details && !loading && !error) {
    return null;
  }
  return (
    <Card
      className="lg:col-span-2"
      aria-label="签名详情"
    >
      <CardHeader className="flex flex-wrap items-center justify-between gap-2">
        <CardTitle>签名详情</CardTitle>
        {details && (
          <Badge className="bg-emerald-50 text-emerald-700">Accepted</Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {loading && <Loading />}
        {error && (
          <div>
            <ErrorBox message={error} />
            <Button
              variant="outline"
              disabled={disabled}
              onClick={onRetry}
            >
              <RefreshCw className="size-4" />
              重试获取详情
            </Button>
          </div>
        )}
        {details && <AcceptedDetails details={details} />}
      </CardContent>
    </Card>
  );
}

function AcceptedDetails({ details }: { details: NotaryDetails }) {
  return (
    <>
      <dl className="grid gap-4 text-[13px] sm:grid-cols-2 xl:grid-cols-3">
        {[
          [
            '公证文件',
            details.archiveFilename,
          ],
          [
            'Apple Job ID',
            details.jobId,
          ],
          [
            '提交时间',
            date(details.uploadDate),
          ],
          [
            '公证说明',
            details.statusSummary,
          ],
          [
            '状态码',
            String(details.statusCode),
          ],
          [
            '日志格式版本',
            String(details.logFormatVersion),
          ],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="mb-1 text-xs text-muted-foreground">{label}</dt>
            <dd className="break-all leading-5">{value}</dd>
          </div>
        ))}
        <div className="sm:col-span-2 xl:col-span-3">
          <dt className="mb-1 text-xs text-muted-foreground">
            公证文件 SHA-256
          </dt>
          <dd className="break-all font-mono text-xs leading-5">
            {details.sha256}
          </dd>
        </div>
      </dl>
      {!!details.issues?.length && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-900">
          <p className="mb-2 font-medium">公证提示</p>
          <ul className="space-y-2">
            {details.issues.map((issue, index) => (
              <li
                // biome-ignore lint/suspicious/noArrayIndexKey: Apple issues are an immutable snapshot without unique IDs and may contain duplicates.
                key={index}
                className="break-all"
              >
                <p>
                  {[
                    issue.severity,
                    issue.code,
                    issue.message,
                  ]
                    .filter((value) => value !== null && value !== undefined)
                    .join(' · ')}
                </p>
                {issue.path && <p className="mt-1 text-xs">{issue.path}</p>}
                {issue.architecture && (
                  <p className="text-xs">{issue.architecture}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {details.ticketContents?.length ? (
        <NotaryTopology details={details} />
      ) : (
        <p className="rounded-md bg-muted p-4 text-[13px] text-muted-foreground">
          公证已通过，Apple 未提供签名票据内容。
        </p>
      )}
    </>
  );
}

const kindLabels = {
  archive: '归档文件',
  bundle: '应用或组件包',
  directory: '目录',
  code: '签名文件',
};

function NotaryTopology({ details }: { details: NotaryDetails }) {
  const topology = useMemo(
    () => buildNotaryTopology(details),
    [
      details,
    ],
  );
  const [selectedId, setSelectedId] = useState('archive');
  const selected =
    topology.nodes.find((node) => node.id === selectedId) || topology.nodes[0];
  return (
    <div className="space-y-3">
      <p className="text-xs leading-5 text-muted-foreground">
        {topology.signedPaths} 个签名路径 · {details.ticketContents?.length}{' '}
        条票据记录。
        按文件包含关系连线；选择节点查看签名，较大的拓扑可滚动浏览。
      </p>
      <fieldset
        aria-label="签名文件拓扑"
        className="max-h-[440px] min-w-0 w-full overflow-auto rounded-md border bg-muted/40"
      >
        <div
          className="relative"
          style={{
            width: topology.width,
            height: topology.height,
          }}
        >
          <svg
            aria-hidden="true"
            width={topology.width}
            height={topology.height}
            className="absolute inset-0 text-slate-300"
          >
            {topology.edges.map((edge) => (
              <path
                key={edge.target}
                data-source={edge.source}
                data-target={edge.target}
                d={edge.line}
                fill="none"
                stroke="currentColor"
                strokeWidth={1.5}
              />
            ))}
          </svg>
          {topology.nodes.map((node) => {
            const Icon =
              node.kind === 'archive'
                ? Package
                : node.kind === 'code'
                  ? FileCode
                  : Folder;
            const architectures = [
              ...new Set(
                node.signatures.map((value) => value.arch || '未提供架构'),
              ),
            ];
            return (
              <button
                key={node.id}
                type="button"
                aria-label={node.path}
                aria-pressed={selected.id === node.id}
                title={node.path}
                onClick={() => setSelectedId(node.id)}
                style={{
                  left: node.x,
                  top: node.y,
                  width: nodeWidth,
                  height: nodeHeight,
                }}
                className={`absolute rounded-md border bg-white px-3 py-2 text-left shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring ${selected.id === node.id ? 'border-primary ring-1 ring-primary' : 'border-border hover:border-slate-400'}`}
              >
                <span className="flex items-center gap-2">
                  <Icon
                    aria-hidden="true"
                    className={`size-4 shrink-0 ${node.signatures.length ? 'text-emerald-700' : 'text-muted-foreground'}`}
                  />
                  <span className="truncate text-[13px] font-medium">
                    {node.name}
                  </span>
                </span>
                <span className="mt-1.5 block truncate text-xs text-muted-foreground">
                  {architectures.length
                    ? architectures.join(' · ')
                    : kindLabels[node.kind]}
                </span>
              </button>
            );
          })}
        </div>
      </fieldset>
      <section
        aria-label="节点签名详情"
        className="rounded-md border p-3 text-[13px]"
      >
        <p className="text-xs text-muted-foreground">
          {kindLabels[selected.kind]} · 完整路径
        </p>
        <p className="mt-1 break-all font-mono text-xs leading-5">
          {selected.path}
        </p>
        {selected.signatures.length ? (
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {selected.signatures.map((signature) => (
              <dl
                key={JSON.stringify([
                  signature.arch,
                  signature.cdhash,
                  signature.digestAlgorithm,
                ])}
                className="space-y-1.5 rounded-md bg-muted p-3 text-xs leading-5"
              >
                <div>
                  <dt className="inline text-muted-foreground">架构：</dt>
                  <dd className="inline">{signature.arch || '未提供'}</dd>
                </div>
                <div>
                  <dt className="inline text-muted-foreground">摘要算法：</dt>
                  <dd className="inline">{signature.digestAlgorithm}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">CDHash</dt>
                  <dd className="break-all font-mono">{signature.cdhash}</dd>
                </div>
                {signature.count > 1 && (
                  <div className="text-muted-foreground">
                    相同记录出现 {signature.count} 次
                  </div>
                )}
              </dl>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-xs text-muted-foreground">
            此节点表示文件层级，Apple 未提供该节点的签名记录。
          </p>
        )}
      </section>
    </div>
  );
}
