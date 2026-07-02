import { motion, useMotionValue, useTransform, useSpring, useMotionTemplate } from "framer-motion";
import React, { useState } from "react";

export const TiltIcon = ({ src, alt, href }: { src: string; alt: string; href: string }) => {
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const [isHovered, setIsHovered] = useState(false);

  // Use springs for smooth elastic interpolation
  const mouseXSpring = useSpring(x, { stiffness: 300, damping: 20 });
  const mouseYSpring = useSpring(y, { stiffness: 300, damping: 20 });

  // Map mouse position to 3D rotation (up to 18 degrees)
  const rotateX = useTransform(mouseYSpring, [-0.5, 0.5], ["18deg", "-18deg"]);
  const rotateY = useTransform(mouseXSpring, [-0.5, 0.5], ["-18deg", "18deg"]);

  // Map mouse position to dynamic drop shadow
  const shadowX = useTransform(mouseXSpring, [-0.5, 0.5], [15, -15]);
  const shadowY = useTransform(mouseYSpring, [-0.5, 0.5], [15, -15]);
  const filter = useMotionTemplate`drop-shadow(0px 0px 50px var(--icon-ambient-glow)) drop-shadow(${shadowX}px ${shadowY}px 20px var(--icon-shadow))`;

  // Map mouse position to liquid glass glare sweep
  const spotX = useTransform(mouseXSpring, [-0.5, 0.5], [0, 100]);
  const spotY = useTransform(mouseYSpring, [-0.5, 0.5], [0, 100]);
  const background = useMotionTemplate`radial-gradient(circle at ${spotX}% ${spotY}%, rgba(255,255,255,var(--glare-center)) 0%, rgba(255,255,255,var(--glare-mid)) 50%, rgba(255,255,255,var(--glare-tail)) 75%, transparent 100%)`;

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement, MouseEvent>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const width = rect.width;
    const height = rect.height;
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;
    
    // Calculate normalized percentage coordinates from center (-0.5 to 0.5)
    x.set(mouseX / width - 0.5);
    y.set(mouseY / height - 0.5);
  };

  const handleMouseLeave = () => {
    setIsHovered(false);
    // Reset rotations gracefully back to 0
    x.set(0);
    y.set(0);
  };

  return (
    <a 
      href={href} 
      target="_blank" 
      rel="noopener noreferrer" 
      className="relative block w-20 h-20 mb-3 cursor-pointer group outline-none"
      onMouseEnter={() => setIsHovered(true)}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{ perspective: 1000 }}
    >
      <motion.div
        animate={{ scale: isHovered ? 1.15 : 1 }}
        transition={{ duration: 0.3, ease: "easeOut" }}
        className="w-full h-full relative pointer-events-none"
        style={{
          rotateX,
          rotateY,
          transformStyle: "preserve-3d",
        }}
      >
        <motion.div
          animate={{
            y: [0, -8, 2, -6, 0],
            x: [0, 2, -2, 1, 0],
            rotate: [0, -2, 1, -1, 0],
          }}
          transition={{
            duration: 8,
            ease: "easeInOut",
            repeat: Infinity,
            repeatType: "mirror",
          }}
          className="w-full h-full relative"
        >
        {/* Base Image with dynamic drop-shadow that correctly hugs transparent edges */}
        <motion.img
          src={src}
          alt={alt}
          className="w-full h-full object-contain pointer-events-none"
          style={{ filter }}
        />

        {/* Glare Effect Overlay */}
        {/* We use mask-image to ensure the glare ONLY renders over non-transparent pixels of the original PNG! */}
        <motion.div
          className="absolute inset-0 pointer-events-none z-10 transition-opacity duration-300"
          style={{
            opacity: isHovered ? 1 : 0,
            WebkitMaskImage: `url(${src})`,
            WebkitMaskSize: "contain",
            WebkitMaskRepeat: "no-repeat",
            WebkitMaskPosition: "center",
            maskImage: `url(${src})`,
            maskSize: "contain",
            maskRepeat: "no-repeat",
            maskPosition: "center",
            background,
          }}
        />
      </motion.div>
      </motion.div>
    </a>
  );
};
