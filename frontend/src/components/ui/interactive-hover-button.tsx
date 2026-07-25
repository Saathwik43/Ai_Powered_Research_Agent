import React from "react";
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface InteractiveHoverButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  text?: string;
  loading?: boolean;
}

const InteractiveHoverButton = React.forwardRef<
  HTMLButtonElement,
  InteractiveHoverButtonProps
>(({ text = "Button", loading = false, className, ...props }, ref) => {
  return (
    <button
      ref={ref}
      className={cn(
        "group relative w-40 cursor-pointer overflow-hidden rounded-full border border-border bg-white shadow-sm p-2 text-center font-semibold",
        className,
      )}
      {...props}
    >
      {loading ? (
        <span className="inline-flex items-center justify-center gap-2">
          {text}
          <span className="flex gap-0.5">
            <span className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.3s]"></span>
            <span className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.15s]"></span>
            <span className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce"></span>
          </span>
        </span>
      ) : (
        <>
          <span className="inline-block translate-x-1 transition-all duration-300 group-hover:translate-x-12 group-hover:opacity-0">
            {text}
          </span>
          <div className="absolute top-0 z-10 flex h-full w-full translate-x-12 items-center justify-center gap-2 text-primary-foreground opacity-0 transition-all duration-300 group-hover:-translate-x-1 group-hover:opacity-100">
            <span>{text}</span>
            <ArrowRight />
          </div>
          <div className="absolute left-[20%] top-[40%] h-2 w-2 scale-[1] rounded-lg bg-primary transition-all duration-300 group-hover:left-[0%] group-hover:top-[0%] group-hover:h-full group-hover:w-full group-hover:scale-[1.8] group-hover:bg-primary"></div>
        </>
      )}
    </button>
  );
});
InteractiveHoverButton.displayName = "InteractiveHoverButton";

export { InteractiveHoverButton };
