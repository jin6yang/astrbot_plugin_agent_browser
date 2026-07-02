import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import AsciiLogo from './AsciiLogo';

interface LoadingScreenProps {
  onFinish: () => void;
  isAppReady: boolean;
}

export default function LoadingScreen({ onFinish, isAppReady }: LoadingScreenProps) {
  const [isFadingOut, setIsFadingOut] = useState(false);
  const [minTimeElapsed, setMinTimeElapsed] = useState(false);
  const [animationComplete, setAnimationComplete] = useState(false);

  useEffect(() => {
    // Minimum wait time is 2.5s
    const minTimer = setTimeout(() => {
      setMinTimeElapsed(true);
    }, 2500);

    return () => clearTimeout(minTimer);
  }, []);

  useEffect(() => {
    if (isAppReady && minTimeElapsed && animationComplete && !isFadingOut) {
      handleOutroComplete();
    }
  }, [isAppReady, minTimeElapsed, animationComplete, isFadingOut]);

  const handleOutroComplete = () => {
    setIsFadingOut(true);
    // Give it 500ms to fade out the background before unmounting entirely
    setTimeout(() => {
      onFinish();
    }, 500);
  };

  return (
    <AnimatePresence>
      {!isFadingOut && (
        <motion.div 
          className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-background text-foreground transition-colors duration-300"
          initial={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.5, ease: "easeInOut" }}
        >
          <AsciiLogo onAnimationComplete={() => setAnimationComplete(true)} />
        </motion.div>
      )}
    </AnimatePresence>
  );
}
