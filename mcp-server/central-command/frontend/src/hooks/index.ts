export {
  useFleet,
  useClient,
  useIncidents,
  useEvents,
  useGlobalStats,
  useLearningStatus,
  useRefreshFleet,
  useRunbooks,
  useRunbook,
  useRunbookExecutions,
  usePromotionCandidates,
  usePromotionHistory,
  usePromotePattern,
  useRejectPattern,
  useOnboardingPipeline,
  useOnboardingMetrics,
  // Sites hooks
  useSites,
  useSite,
  useCreateSite,
  useUpdateSite,
  useAddCredential,
  useDeleteCredential,
  useDeleteSite,
  useUpdateHealingTier,
  // Order hooks
  useOrders,
  useCreateApplianceOrder,
  useBroadcastOrder,
  useDeleteAppliance,
  useClearStaleAppliances,
  // Notification hooks
  useNotifications,
  useNotificationSummary,
  useMarkNotificationRead,
  useMarkAllNotificationsRead,
  useDismissNotification,
  useCreateNotification,
  // Runbook config hooks
  useRunbookCatalog,
  useSiteRunbookConfig,
  useSetSiteRunbook,
  useRunbookCategories,
  // Workstation hooks
  useSiteWorkstations,
  useTriggerWorkstationScan,
  // Go Agent hooks
  useSiteGoAgents,
  useUpdateGoAgentTier,
  useTriggerGoAgentCheck,
  useRemoveGoAgent,
  // Device inventory hooks
  useSiteDevices,
  useSiteDeviceSummary,
  useSiteMedicalDevices,
  // CVE Watch hooks
  useCVESummary,
  useCVEs,
  useCVEDetail,
  useTriggerCVESync,
  useUpdateCVEStatus,
  useCVEWatchConfig,
} from './useFleet';

export { useKeyboardShortcuts, useCommandPalette } from './useKeyboardShortcuts';
export { useWebSocket, useWebSocketStatus, WebSocketContext } from './useWebSocket';

// Integrations hooks
export {
  useIntegrations,
  useIntegration,
  useCreateIntegration,
  useDeleteIntegration,
  useIntegrationResources,
  useTriggerSync,
  useSyncJob,
  useIntegrationsHealth,
  useAWSSetupInstructions,
  useGenerateAWSExternalId,
  useRefreshIntegrations,
  useRefreshResources,
} from './useIntegrations';

export { useDeploymentStatus } from './useDeployment';
