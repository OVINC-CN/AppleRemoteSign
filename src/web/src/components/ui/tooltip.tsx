import { Tooltip as TooltipPrimitive } from 'radix-ui';
import type { ReactElement } from 'react';

export function Tooltip({
  children,
  text,
}: {
  children: ReactElement;
  text: string;
}) {
  if (!text) {
    return children;
  }
  return (
    <TooltipPrimitive.Provider>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            sideOffset={5}
            className="z-50 max-w-[min(24rem,calc(100vw-2rem))] break-words rounded-md bg-primary px-2 py-1 text-xs text-primary-foreground shadow-md"
          >
            {text}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}
