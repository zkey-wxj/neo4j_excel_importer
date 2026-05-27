import { clsx } from "clsx";
import { twMerge } from "tailwind-merge"

/**
 * 合并 Tailwind CSS 类名工具函数
 * 先通过 clsx 条件性拼接类名，再通过 twMerge 解决 Tailwind 类冲突
 * @param  {...string} inputs - 类名或条件类名表达式
 * @returns {string} 合并后的类名字符串
 */
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
