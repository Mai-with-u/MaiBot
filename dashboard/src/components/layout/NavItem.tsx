import { Link, useMatchRoute } from '@tanstack/react-router'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useCallback, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/utils'

import type { MenuItem } from './types'

const MotionLink = motion.create(Link)

interface NavItemProps {
  item: MenuItem
  sidebarOpen: boolean
  expandOnHover?: boolean
  onMobileMenuClose: () => void
}

export function NavItem({
  item,
  sidebarOpen,
  expandOnHover = false,
  onMobileMenuClose,
}: NavItemProps) {
  const { t } = useTranslation()
  const matchRoute = useMatchRoute()
  const isActive = item.external ? false : matchRoute({ to: item.path })
  const Icon = item.icon
  const label = item.literalLabel ? item.label : t(item.label)
  const prefersReducedMotion = useReducedMotion()
  const flyoutTransition = {
    duration: prefersReducedMotion ? 0 : 0.22,
    ease: [0.22, 1, 0.36, 1] as const,
  }
  const itemRef = useRef<HTMLLIElement>(null)
  const flyoutRef = useRef<HTMLDivElement>(null)
  const [flyoutRect, setFlyoutRect] = useState<DOMRect | null>(null)
  const [flyoutMounted, setFlyoutMounted] = useState(false)
  // 原位按钮和悬浮按钮是两份 DOM。浮层可能在「按下」和「抬起」之间才盖上来（或刚好收走），
  // 这时两次事件落在不同元素上，浏览器不会派发 click。用这两个标记识别这种情况并补一次点击。
  const pressStartedRef = useRef(false)
  const clickDeliveredRef = useRef(false)

  const handleFlyoutScroll = useCallback(() => {
    const node = flyoutRef.current
    const link = itemRef.current?.firstElementChild
    if (node && link) {
      // 退出动画仍会保留 DOM；每次滚动都跟随原按钮更新位置，直到动画结束卸载。
      const rect = link.getBoundingClientRect()
      node.style.left = `${rect.left - 6}px`
      node.style.top = `${rect.top - 6}px`
    }
    setFlyoutRect(null)
  }, [])

  const handleFlyoutWheel = useCallback((event: WheelEvent) => {
    if (event.ctrlKey || event.deltaY === 0) return
    const viewport = itemRef.current?.closest<HTMLElement>('[data-dashboard-scrollbar-viewport="true"]')
    if (!viewport) return

    // Portal 不在滚动容器内，将滚轮交回原侧栏；兼容鼠标的行单位和触控板的像素单位。
    event.preventDefault()
    event.stopPropagation()
    const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE
      ? 16
      : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
        ? viewport.clientHeight
        : 1
    viewport.scrollTop += event.deltaY * unit
  }, [])

  const setFlyoutRef = useCallback((node: HTMLDivElement | null) => {
    flyoutRef.current?.removeEventListener('wheel', handleFlyoutWheel)
    window.removeEventListener('scroll', handleFlyoutScroll, true)
    window.removeEventListener('resize', handleFlyoutScroll)
    flyoutRef.current = node
    // 原按钮保留布局和交互，但浮层实际存在期间只绘制浮层，包含退出动画。
    setFlyoutMounted(node !== null)
    // 使用非 passive 原生监听，确保可以阻止浮层下方页面的默认滚动。
    node?.addEventListener('wheel', handleFlyoutWheel, { passive: false })
    if (node) {
      // 监听与浮层 DOM 同寿命，收回动画期间也继续跟随滚动及窗口尺寸变化。
      window.addEventListener('scroll', handleFlyoutScroll, true)
      window.addEventListener('resize', handleFlyoutScroll)
    }
  }, [handleFlyoutScroll, handleFlyoutWheel])

  const openFlyout = () => {
    if (expandOnHover && window.matchMedia('(min-width: 1024px)').matches) {
      const link = itemRef.current?.firstElementChild
      if (link) setFlyoutRect(link.getBoundingClientRect())
    }
  }

  const menuItemContent = (expanded = false) => (
    <>
      <div className={cn(
        'flex min-w-0 items-center',
        sidebarOpen || expanded ? 'gap-3' : 'gap-3 lg:gap-0',
        expanded && 'shrink-0'
      )}>
        <Icon
          data-dashboard-nav-icon="true"
          className={cn('h-5 w-5 flex-shrink-0', isActive && 'text-primary')}
          size={20}
        />
        <motion.span
          data-dashboard-nav-label="true"
          initial={expanded ? { opacity: 0 } : false}
          animate={expanded ? { opacity: 1 } : undefined}
          exit={expanded ? { opacity: 0 } : undefined}
          transition={flyoutTransition}
          className={cn(
            'text-base font-medium whitespace-nowrap transition-opacity duration-150',
            expanded
              ? 'opacity-100'
              : sidebarOpen
                ? 'max-w-[160px] min-w-0 overflow-hidden text-ellipsis opacity-100'
                : 'max-w-[200px] opacity-100 lg:max-w-0 lg:overflow-hidden lg:opacity-0'
          )}
          title={expanded ? undefined : label}
        >
          {label}
        </motion.span>
      </div>
    </>
  )

  const linkClassName = cn(
    'relative flex h-[var(--layout-sidebar-nav-item-height)] items-center rounded-lg px-[var(--layout-sidebar-nav-item-padding-x)] py-0 transition-colors duration-150',
    'hover:bg-accent hover:text-accent-foreground',
    isActive ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground',
    sidebarOpen &&
      'lg:pl-[calc(var(--layout-sidebar-nav-icon-left)-var(--layout-sidebar-nav-padding-collapsed))]',
    !sidebarOpen &&
      'lg:w-[var(--layout-sidebar-nav-item-collapsed-width)] lg:justify-center lg:px-0'
  )
  const commonLinkProps = {
    'data-tour': item.tourId,
    'data-dashboard-nav-item': 'true',
    'data-active': isActive ? 'true' : 'false',
    'aria-label': label,
    style: {
      height: 'var(--layout-sidebar-nav-item-height)',
      minHeight: 'var(--layout-sidebar-nav-item-height)',
      opacity: flyoutMounted ? 0 : 1,
    },
    className: linkClassName,
    onClick: () => {
      clickDeliveredRef.current = true
      setFlyoutRect(null)
      onMobileMenuClose()
    },
    onKeyDown: (event: KeyboardEvent<HTMLAnchorElement>) => {
      if (event.key === 'Escape') setFlyoutRect(null)
    },
  } as const

  const renderLink = (expanded = false) => {
    const linkProps =
      expanded && flyoutRect
        ? {
            ...commonLinkProps,
            className: cn(linkClassName, 'overflow-hidden lg:w-max lg:justify-start'),
            style: {
              ...commonLinkProps.style,
              opacity: 1,
              minWidth: flyoutRect.width,
              paddingLeft: (flyoutRect.width - 20) / 2,
              paddingRight: 12,
              transform: 'none',
            },
            tabIndex: -1,
          }
        : commonLinkProps
    if (expanded && flyoutRect) {
      // 动画直接改变键帽本身的宽度，四周边框和底边随之重绘，不再裁断右边缘。
      const animationProps = {
        initial: { width: flyoutRect.width },
        animate: { width: 'auto' },
        exit: { width: flyoutRect.width },
        transition: flyoutTransition,
      }
      return item.external ? (
        <motion.a href={item.path} target="_blank" rel="noopener noreferrer" {...linkProps} {...animationProps}>
          {menuItemContent(true)}
        </motion.a>
      ) : (
        <MotionLink to={item.path} {...linkProps} {...animationProps}>
          {menuItemContent(true)}
        </MotionLink>
      )
    }
    return item.external ? (
      <a href={item.path} target="_blank" rel="noopener noreferrer" {...linkProps}>
        {menuItemContent(expanded)}
      </a>
    ) : (
      <Link to={item.path} {...linkProps}>
        {menuItemContent(expanded)}
      </Link>
    )
  }

  return (
    <li
      ref={itemRef}
      className="relative"
      onPointerEnter={(event) => {
        if (event.pointerType === 'mouse') openFlyout()
      }}
      onPointerLeave={() => setFlyoutRect(null)}
      onPointerDown={(event) => {
        const onNavLink = (event.target as Element).closest('[data-dashboard-nav-item]') !== null
        pressStartedRef.current = event.button === 0 && onNavLink
        clickDeliveredRef.current = false
      }}
      onPointerUp={(event) => {
        if (!pressStartedRef.current) return
        pressStartedRef.current = false
        // 带修饰键的点击（新标签页打开等）交给浏览器默认行为，不代为触发。
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
          return
        }
        const releasedLink = (event.target as Element).closest<HTMLElement>(
          '[data-dashboard-nav-item]'
        )
        if (!releasedLink) return
        window.setTimeout(() => {
          if (clickDeliveredRef.current) return
          // 正常情况下 click 已经在这之前送达；没送达说明按下和抬起落在了两份按钮上。
          const link = releasedLink.isConnected
            ? releasedLink
            : (itemRef.current?.firstElementChild as HTMLElement | null)
          link?.click()
        }, 0)
      }}
      onPointerCancel={() => {
        pressStartedRef.current = false
      }}
      onFocus={openFlyout}
      onBlur={(event) => {
        if (!itemRef.current?.contains(event.relatedTarget) && !flyoutRef.current?.contains(event.relatedTarget)) {
          setFlyoutRect(null)
        }
      }}
    >
      {renderLink()}
      {/* 浮层保留完整可点击按钮，越过侧栏裁剪和滚动容器，且不占用页面布局宽度。 */}
      {expandOnHover &&
        createPortal(
          <AnimatePresence>
            {flyoutRect && (
              <motion.div
                key={item.path}
                ref={setFlyoutRef}
                className="fixed z-[60] p-[6px] max-lg:hidden"
                style={{ left: flyoutRect.left - 6, top: flyoutRect.top - 6 }}
                // 外层只定位并保留键帽阴影，收回时等待内部按钮的宽度动画结束。
                exit={{}}
              >
                {renderLink(true)}
              </motion.div>
            )}
          </AnimatePresence>,
          document.body
        )}
    </li>
  )
}
