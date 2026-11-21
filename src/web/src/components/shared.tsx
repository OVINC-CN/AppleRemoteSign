import { AlertCircle, Inbox, LoaderCircle } from 'lucide-react';
import type { ReactNode } from 'react';
import { type JobStatus, statusLabels } from '@/lib/types';
import { Badge } from './ui/badge';

export function PageHeading({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold leading-8 tracking-tight">
          {title}
        </h1>
        <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
          {description}
        </p>
      </div>
      {action}
    </div>
  );
}
export function ErrorBox({ message }: { message: string }) {
  return message ? (
    <div
      role="alert"
      className="my-3 flex gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2.5 text-[13px] leading-5 text-red-800"
    >
      <AlertCircle className="size-4 shrink-0 mt-0.5" />
      <span className="break-words">{message}</span>
    </div>
  ) : null;
}
export function Loading() {
  return (
    <div
      role="status"
      className="flex items-center justify-center gap-2 p-10 text-[13px] text-muted-foreground"
    >
      <LoaderCircle
        aria-hidden="true"
        className="size-4 animate-spin"
      />
      正在加载…
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center px-4 py-12 text-center">
      <Inbox
        aria-hidden="true"
        className="mb-3 size-7 text-slate-300"
      />
      <h2 className="text-sm font-medium">{title}</h2>
      <div className="mt-1.5 text-[13px] leading-5 text-muted-foreground">
        {children}
      </div>
    </div>
  );
}
export function StatusBadge({ status }: { status: JobStatus }) {
  const colors = {
    preparing: 'bg-slate-100 text-slate-700',
    processing: 'bg-blue-50 text-blue-700',
    completed: 'bg-emerald-50 text-emerald-700',
    failed: 'bg-red-50 text-red-700',
  };
  return (
    <Badge
      variant="outline"
      className={`h-5 gap-1.5 rounded-md border-transparent px-2 py-0 text-xs leading-4 ${colors[status]}`}
    >
      <span
        className={`size-1.5 rounded-full bg-current ${status === 'processing' ? 'animate-pulse' : ''}`}
      />
      {statusLabels[status]}
    </Badge>
  );
}
