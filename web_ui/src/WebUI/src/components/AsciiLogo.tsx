import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';

const ASCII_ART = [
  "                                               ...........            ",
  "                                               *@@@@@@@@@+            ",
  "                                               *@@@@@@@@@+            ",
  "            -++++++++++++++++++++++++++++++++==#@@@@@@@@@+            ",
  "            *@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@%##-.......             ",
  "            *@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@%##:                    ",
  "            *@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@-                    ",
  "            *@%######%@@######%@@@@@@@@@@@@@@@@@@=                    ",
  "            *@*@@@@@@*@#@@@@@@@@@@@@@@@@@@@@@@@@@=                    ",
  "            *@*@@@@@@+@#@@@@@@@%@@@@@@@@@@@@@@@@@=                    ",
  "            *@+@@@@@@+%*@@@@@@@%@@@@@@@@@@@@@@@@@=                    ",
  "            +%+@@@@@@+#*@@@@@@@%%@@@@@@@@@@@@@@@@=                    ",
  "            +%+@@@@@@+#*@@@@@@@%%@@@@@@@@@@@@@@@@=                    ",
  "            *@*@@@@@@+#*@@@@@@@%@@@@@@@@@@@@@@@@@=                    ",
  "            =#*+====++*+++===++*#################-                    "
];

interface AsciiLogoProps {
  onAnimationComplete?: () => void;
}

export default function AsciiLogo({ onAnimationComplete }: AsciiLogoProps) {
  useEffect(() => {
    if (onAnimationComplete) {
      onAnimationComplete();
    }
  }, [onAnimationComplete]);

  // State machine for continuous random animations
  const [anim, setAnim] = useState({
    bodyY: { current: 0, target: 0, phase: 'idle', timer: 5 },
    eyeX: { current: 0, target: 0, phase: 'idle', timer: 15 },
    eyeY: { current: 0, target: 0, phase: 'idle', timer: 25 },
  });

  useEffect(() => {
    const interval = setInterval(() => {
      setAnim(prev => {
        const updateAxis = (axis: any, maxIdle: number, stretchDuration: number) => {
          let next = { ...axis };
          if (next.phase === 'idle') {
            next.timer--;
            if (next.timer <= 0) {
              const options = [-1, 0, 1].filter(v => v !== next.current);
              next.target = options[Math.floor(Math.random() * options.length)];
              next.phase = 'stretch';
              next.timer = stretchDuration;
            }
          } else if (next.phase === 'stretch') {
            next.timer--;
            if (next.timer <= 0) {
              next.current = next.target;
              next.phase = 'idle';
              next.timer = 5 + Math.floor(Math.random() * maxIdle);
            }
          }
          return next;
        };

        return {
          bodyY: updateAxis(prev.bodyY, 20, 5), // 500ms stretch, 0.5-2.5s idle
          eyeX: updateAxis(prev.eyeX, 15, 4),   // 400ms stretch, 0.5-2.0s idle
          eyeY: updateAxis(prev.eyeY, 15, 4),
        };
      });
    }, 100); // 10fps for chunky retro terminal feel
    return () => clearInterval(interval);
  }, []);

  // Compute Layout bounds based on state machine
  const bY = anim.bodyY;
  const bodyActiveTop = bY.phase === 'stretch' ? Math.min(bY.current, bY.target) : bY.current;
  const stretchCount = bY.phase === 'stretch' ? Math.abs(bY.target - bY.current) : 0;

  const eX = anim.eyeX;
  const eY = anim.eyeY;
  const eyeActiveTop = eY.phase === 'stretch' ? Math.min(eY.current, eY.target) : eY.current;
  const eyeActiveBottom = eY.phase === 'stretch' ? Math.max(eY.current, eY.target) : eY.current;
  const eyeActiveLeft = eX.phase === 'stretch' ? Math.min(eX.current, eX.target) : eX.current;
  const eyeActiveRight = eX.phase === 'stretch' ? Math.max(eX.current, eX.target) : eX.current;

  // Construct current art with optional body stretch
  let currentArt = ASCII_ART.map((str, i) => ({ str, origY: i }));
  if (stretchCount > 0) {
    const stretchRow = bY.target > bY.current ? 11 : 6;
    for (let i = 0; i < stretchCount; i++) {
      currentArt.splice(stretchRow, 0, { str: ASCII_ART[stretchRow], origY: stretchRow });
    }
  }

  // Pad the grid to keep the height fixed at 17
  const totalRows = 17;
  const emptyRow = " ".repeat(70);
  const visibleRows = [];
  for (let i = 0; i < totalRows; i++) {
    const artIndex = i - (1 + bodyActiveTop);
    if (artIndex >= 0 && artIndex < currentArt.length) {
      visibleRows.push(currentArt[artIndex]);
    } else {
      visibleRows.push(null);
    }
  }

  const renderChar = (char: string, x: number, origY: number | null) => {
    if (char === ' ') return ' ';

    if (origY !== null) {
      // Physical eye carving with caterpillar stretching
      const inLeftEye = 
         origY >= 8 + eyeActiveTop && origY <= 13 + eyeActiveBottom &&
         x >= 15 + eyeActiveLeft && x <= 20 + eyeActiveRight;

      const inRightEye = 
         origY >= 8 + eyeActiveTop && origY <= 13 + eyeActiveBottom &&
         x >= 24 + eyeActiveLeft && x <= 30 + eyeActiveRight;

      if (inLeftEye || inRightEye) {
        return ' ';
      }

      const isBody = char === '@' || char === '#' || char === '%';
      const isEdge = char === '*' || char === '+' || char === '=' || char === '-' || char === ':' || char === '.';

      let finalChar = char;
      let colorClass = 'text-default-900 dark:text-default-100';

      if (isBody) {
        // Occasional body static
        if (Math.random() < 0.005) {
          finalChar = char === '@' ? '$' : '@';
        }
      } else if (isEdge) {
        // Solid, stable edge color (no flashing)
        colorClass = 'text-default-400 dark:text-default-600';
        
        // Static coordinate-based thematic highlight
        if ((x + origY) % 7 === 0) {
          colorClass = 'text-primary drop-shadow-[0_0_8px_rgba(var(--nextui-primary),0.8)]';
        }
      }

      return (
        <span key={`${x}-${origY}`} className={colorClass}>
          {finalChar}
        </span>
      );
    }
    
    return char;
  };

  return (
    <div className="flex flex-col items-center justify-center font-josefin">
      <div className="text-left bg-black/5 dark:bg-black/20 p-8 rounded-xl border border-black/10 dark:border-white/10 shadow-2xl backdrop-blur-md">
        
        <pre
          className="text-xs sm:text-sm md:text-base lg:text-lg leading-[1.1] font-mono font-bold tracking-tighter whitespace-pre select-all drop-shadow-[0_0_10px_rgba(255,255,255,0.1)] dark:drop-shadow-[0_0_10px_rgba(255,255,255,0.2)]"
        >
          {visibleRows.map((rowObj, y) => {
            const rowStr = rowObj ? rowObj.str : emptyRow;
            const origY = rowObj ? rowObj.origY : null;
            return (
              <React.Fragment key={`row-${y}`}>
                {rowStr.split('').map((char, x) => renderChar(char, x, origY))}
                {'\n'}
              </React.Fragment>
            );
          })}
        </pre>
        
      </div>
      
      <motion.div 
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.2 }}
        className="mt-8 text-default-500 font-mono flex flex-col items-center gap-1"
      >
        <p>[ OK ] Booting Obscura Agent Browser...</p>
        <p>[ OK ] Loading environment</p>
        <p className="animate-pulse text-primary font-semibold">[ RUN ] Initializing sequence...</p>
      </motion.div>
    </div>
  );
}
