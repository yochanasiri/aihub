import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';
const buttonVariants = cva('inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-amber-400 [&_svg]:size-4 [&_svg]:shrink-0', {
  variants: { variant: { default:'bg-[#2f6bff] text-white hover:bg-blue-500', secondary:'border border-slate-700 bg-slate-800/50 text-slate-200 hover:bg-slate-800', ghost:'text-slate-400 hover:bg-slate-800 hover:text-white', gold:'bg-[#f5b335] text-slate-950 hover:bg-amber-300', destructive:'border border-red-500/50 bg-red-950/40 text-red-200 hover:bg-red-900/60' }, size: { default:'h-10 px-4 py-2', sm:'h-8 px-3 text-xs', icon:'size-10' } }, defaultVariants: { variant:'default',size:'default' }
});
function Button({className,variant,size,asChild=false,...props}:React.ComponentProps<'button'> & VariantProps<typeof buttonVariants> & {asChild?:boolean}) {
 const Comp=asChild?Slot:'button'; return <Comp data-slot="button" className={cn(buttonVariants({variant,size,className}))} {...props}/>;
}
export { Button, buttonVariants };
