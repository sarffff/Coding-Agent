import { useCallback, useEffect, useRef, useState } from "react";

export function useChatScrollPinning(revision: unknown, conversationKey: string) {
  const containerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  const lastUserInputAt = useRef(-Infinity);
  const [showJumpControl, setShowJumpControl] = useState(false);

  const updatePosition = useCallback(() => {
    const element = containerRef.current;
    if (!element) return;
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
    pinnedRef.current = distance <= 40;
    setShowJumpControl(distance > 120);
  }, []);

  const markUserInput = useCallback(() => {
    lastUserInputAt.current = performance.now();
  }, []);

  const scrollToBottom = useCallback(() => {
    const element = containerRef.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior: "auto" });
    lastUserInputAt.current = -Infinity;
    pinnedRef.current = true;
    setShowJumpControl(false);
  }, []);

  const followLatest = useCallback(() => {
    if (pinnedRef.current && performance.now() - lastUserInputAt.current > 150) scrollToBottom();
    else {
      const element = containerRef.current;
      if (element) setShowJumpControl(element.scrollHeight - element.scrollTop - element.clientHeight > 120);
    }
  }, [scrollToBottom]);

  useEffect(() => {
    pinnedRef.current = true;
    lastUserInputAt.current = -Infinity;
    scrollToBottom();
  }, [conversationKey, scrollToBottom]);

  useEffect(followLatest, [revision, followLatest]);

  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const observer = new ResizeObserver(followLatest);
    observer.observe(content);
    return () => observer.disconnect();
  }, [revision, conversationKey, followLatest]);

  return { containerRef, contentRef, showJumpControl, markUserInput, updatePosition, scrollToBottom };
}
