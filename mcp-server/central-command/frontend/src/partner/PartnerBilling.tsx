import React, { useState, useEffect } from 'react';
import { usePartner } from './PartnerContext';

interface ApplianceTier {
  name: string;
  price_monthly: number;
  max_endpoints: number | null;
  description: string;
  features: string[];
}

interface PaymentMethod {
  id: string;
  brand: string;
  last4: string;
  exp_month: number;
  exp_year: number;
  is_default: boolean;
}

interface Invoice {
  id: string;
  number: string;
  status: string;
  amount_due: number;
  amount_paid: number;
  currency: string;
  created: string;
  due_date: string | null;
  paid_at: string | null;
  invoice_pdf: string | null;
  hosted_invoice_url: string | null;
}

interface BillingStatus {
  has_subscription: boolean;
  subscription_status: string;
  subscription_plan: string | null;
  subscription: {
    id: string;
    status: string;
    current_period_start: string;
    current_period_end: string;
    cancel_at_period_end: boolean;
    plan: {
      id: string;
      amount: number;
      interval: string;
    } | null;
  } | null;
  upcoming_invoice: {
    amount_due: number;
    currency: string;
    next_payment_date: string | null;
  } | null;
  payment_methods: PaymentMethod[];
  trial_ends_at: string | null;
}

interface BillingConfig {
  stripe_publishable_key: string;
  pricing_model: string;
  endpoint_price_monthly: number;
  appliance_tiers: Record<string, ApplianceTier>;
  value_comparison: Record<string, { monthly: number; description: string }>;
  currency: string;
}

export const PartnerBilling: React.FC = () => {
  const { apiKey } = usePartner();
  const [billingStatus, setBillingStatus] = useState<BillingStatus | null>(null);
  const [billingConfig, setBillingConfig] = useState<BillingConfig | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchOptions: RequestInit = apiKey
    ? { headers: { 'X-API-Key': apiKey } }
    : { credentials: 'include' };

  useEffect(() => {
    loadBillingData();
  }, []);

  const loadBillingData = async () => {
    setLoading(true);
    setError(null);

    try {
      const [statusRes, configRes, invoicesRes] = await Promise.all([
        fetch('/api/billing/status', fetchOptions),
        fetch('/api/billing/config'),
        fetch('/api/billing/invoices', fetchOptions),
      ]);

      if (statusRes.ok) {
        setBillingStatus(await statusRes.json());
      }

      if (configRes.ok) {
        setBillingConfig(await configRes.json());
      }

      if (invoicesRes.ok) {
        const data = await invoicesRes.json();
        setInvoices(data.invoices || []);
      }
    } catch (e) {
      setError('Failed to load billing information');
      console.error('Billing fetch error:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleSubscribe = async (priceId: string) => {
    setActionLoading(true);
    try {
      const response = await fetch('/api/billing/checkout', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        },
        credentials: apiKey ? undefined : 'include',
        body: JSON.stringify({ price_id: priceId }),
      });

      if (response.ok) {
        const data = await response.json();
        // Redirect to Stripe Checkout
        window.location.href = data.checkout_url;
      } else {
        const err = await response.json();
        setError(err.detail || 'Failed to create checkout session');
      }
    } catch (e) {
      setError('Failed to create checkout session');
    } finally {
      setActionLoading(false);
    }
  };

  const handleManageBilling = async () => {
    setActionLoading(true);
    try {
      const response = await fetch('/api/billing/portal', {
        method: 'POST',
        headers: apiKey ? { 'X-API-Key': apiKey } : {},
        credentials: apiKey ? undefined : 'include',
      });

      if (response.ok) {
        const data = await response.json();
        // Redirect to Stripe Customer Portal
        window.location.href = data.portal_url;
      } else {
        const err = await response.json();
        setError(err.detail || 'Failed to open billing portal');
      }
    } catch (e) {
      setError('Failed to open billing portal');
    } finally {
      setActionLoading(false);
    }
  };

  const handleCancelSubscription = async () => {
    if (!confirm('Are you sure you want to cancel your subscription? You will still have access until the end of your billing period.')) {
      return;
    }

    setActionLoading(true);
    try {
      const response = await fetch('/api/billing/subscription/cancel', {
        method: 'POST',
        headers: apiKey ? { 'X-API-Key': apiKey } : {},
        credentials: apiKey ? undefined : 'include',
      });

      if (response.ok) {
        loadBillingData();
      } else {
        const err = await response.json();
        setError(err.detail || 'Failed to cancel subscription');
      }
    } catch (e) {
      setError('Failed to cancel subscription');
    } finally {
      setActionLoading(false);
    }
  };

  const handleReactivate = async () => {
    setActionLoading(true);
    try {
      const response = await fetch('/api/billing/subscription/reactivate', {
        method: 'POST',
        headers: apiKey ? { 'X-API-Key': apiKey } : {},
        credentials: apiKey ? undefined : 'include',
      });

      if (response.ok) {
        loadBillingData();
      } else {
        const err = await response.json();
        setError(err.detail || 'Failed to reactivate subscription');
      }
    } catch (e) {
      setError('Failed to reactivate subscription');
    } finally {
      setActionLoading(false);
    }
  };

  const formatCurrency = (amount: number, currency: string = 'usd') => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency.toUpperCase(),
    }).format(amount / 100);
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'active':
        return 'bg-green-100 text-green-800';
      case 'trialing':
        return 'bg-blue-100 text-blue-800';
      case 'past_due':
        return 'bg-red-100 text-red-800';
      case 'canceling':
        return 'bg-yellow-100 text-yellow-800';
      case 'canceled':
      case 'none':
        return 'bg-gray-100 text-gray-800';
      default:
        return 'bg-gray-100 text-gray-800';
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 rounded-xl flex items-center justify-center animate-pulse-soft" style={{ background: 'linear-gradient(135deg, #4F46E5, #7C3AED)' }} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Error Banner */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-center gap-3">
          <svg className="w-5 h-5 text-red-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <p className="text-red-700">{error}</p>
          <button onClick={() => setError(null)} className="ml-auto text-red-500 hover:text-red-700">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      {/* Current Subscription Status */}
      {billingStatus && (
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
          <div className="flex items-center justify-between mb-6">
            <h3 className="text-lg font-semibold text-gray-900">Subscription Status</h3>
            <span className={`px-3 py-1 text-sm font-medium rounded-full ${getStatusColor(billingStatus.subscription_status)}`}>
              {billingStatus.subscription_status === 'none' ? 'No Subscription' : billingStatus.subscription_status}
            </span>
          </div>

          {billingStatus.has_subscription && billingStatus.subscription ? (
            <div className="space-y-4">
              {/* Active Subscription Details */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="bg-gray-50 rounded-lg p-4">
                  <p className="text-sm text-gray-500 mb-1">Current Plan</p>
                  <p className="text-xl font-semibold text-gray-900">
                    {billingStatus.subscription.plan?.amount
                      ? formatCurrency(billingStatus.subscription.plan.amount)
                      : '$--'}
                    <span className="text-sm font-normal text-gray-500">
                      /{billingStatus.subscription.plan?.interval || 'month'}
                    </span>
                  </p>
                </div>

                <div className="bg-gray-50 rounded-lg p-4">
                  <p className="text-sm text-gray-500 mb-1">Current Period Ends</p>
                  <p className="text-xl font-semibold text-gray-900">
                    {formatDate(billingStatus.subscription.current_period_end)}
                  </p>
                </div>

                {billingStatus.upcoming_invoice && (
                  <div className="bg-gray-50 rounded-lg p-4">
                    <p className="text-sm text-gray-500 mb-1">Next Payment</p>
                    <p className="text-xl font-semibold text-gray-900">
                      {formatCurrency(billingStatus.upcoming_invoice.amount_due, billingStatus.upcoming_invoice.currency)}
                    </p>
                    {billingStatus.upcoming_invoice.next_payment_date && (
                      <p className="text-xs text-gray-500 mt-1">
                        on {formatDate(billingStatus.upcoming_invoice.next_payment_date)}
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* Cancellation Notice */}
              {billingStatus.subscription.cancel_at_period_end && (
                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                  <div className="flex items-center gap-3">
                    <svg className="w-5 h-5 text-yellow-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                    </svg>
                    <div>
                      <p className="font-medium text-yellow-800">Subscription Canceling</p>
                      <p className="text-sm text-yellow-700">
                        Your subscription will end on {formatDate(billingStatus.subscription.current_period_end)}.
                      </p>
                    </div>
                    <button
                      onClick={handleReactivate}
                      disabled={actionLoading}
                      className="ml-auto px-4 py-2 bg-yellow-600 text-white font-medium rounded-lg hover:bg-yellow-700 disabled:opacity-50 transition"
                    >
                      Reactivate
                    </button>
                  </div>
                </div>
              )}

              {/* Action Buttons */}
              <div className="flex gap-3 pt-4 border-t">
                <button
                  onClick={handleManageBilling}
                  disabled={actionLoading}
                  className="px-4 py-2 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition"
                >
                  Manage Billing
                </button>
                {!billingStatus.subscription.cancel_at_period_end && (
                  <button
                    onClick={handleCancelSubscription}
                    disabled={actionLoading}
                    className="px-4 py-2 text-red-600 hover:text-red-800 font-medium transition"
                  >
                    Cancel Subscription
                  </button>
                )}
              </div>
            </div>
          ) : (
            /* No Subscription - Show Appliance Tiers */
            <div>
              <p className="text-gray-600 mb-2">
                Deploy OsirisCare appliances for HIPAA compliance monitoring, operator-authorized remediation, and audit-ready evidence.
              </p>
              <p className="text-sm text-gray-500 mb-6">
                Per-appliance pricing • Each site/domain requires one appliance
              </p>

              {billingConfig && (
                <>
                  {/* Value Comparison Banner */}
                  <div className="bg-green-50 border border-green-200 rounded-lg p-4 mb-6">
                    <div className="flex items-center gap-3">
                      <svg className="w-6 h-6 text-green-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      <div>
                        <p className="font-medium text-green-800">Save up to 80% vs. Traditional MSP + Compliance</p>
                        <p className="text-sm text-green-700">
                          Traditional cost: ~${billingConfig.value_comparison.total_traditional?.monthly.toLocaleString()}/mo per site
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Appliance Tiers */}
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    {Object.entries(billingConfig.appliance_tiers).map(([key, tier]) => (
                      <div
                        key={key}
                        className={`border-2 rounded-xl p-6 ${
                          key === 'practice' ? 'border-indigo-500 ring-2 ring-indigo-100' : 'border-gray-200'
                        }`}
                      >
                        {key === 'practice' && (
                          <span className="inline-block px-3 py-1 text-xs font-semibold text-indigo-600 bg-indigo-100 rounded-full mb-4">
                            Most Popular
                          </span>
                        )}
                        <h4 className="text-xl font-bold text-gray-900">{tier.name}</h4>
                        <p className="text-sm text-gray-500 mb-2">{tier.description}</p>
                        <div className="mt-2 mb-4">
                          <span className="text-3xl font-bold text-gray-900">${tier.price_monthly}</span>
                          <span className="text-gray-500">/mo per appliance</span>
                        </div>
                        <p className="text-sm text-gray-600 mb-4 font-medium">
                          {tier.max_endpoints ? `Up to ${tier.max_endpoints} endpoints` : 'Unlimited endpoints'}
                        </p>
                        <ul className="space-y-2 mb-6">
                          {tier.features.slice(0, 5).map((feature, i) => (
                            <li key={i} className="flex items-center gap-2 text-sm text-gray-600">
                              <svg className="w-4 h-4 text-green-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                              </svg>
                              {feature}
                            </li>
                          ))}
                          {tier.features.length > 5 && (
                            <li className="text-sm text-indigo-600 font-medium">
                              +{tier.features.length - 5} more features
                            </li>
                          )}
                        </ul>
                        <button
                          onClick={() => handleSubscribe(`price_appliance_${key}_monthly`)}
                          disabled={actionLoading}
                          className={`w-full py-2 px-4 font-medium rounded-lg transition disabled:opacity-50 ${
                            key === 'practice'
                              ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                              : 'bg-gray-100 text-gray-900 hover:bg-gray-200'
                          }`}
                        >
                          {actionLoading ? 'Loading...' : 'Get Started'}
                        </button>
                      </div>
                    ))}
                  </div>

                  {/* Per-Endpoint Alternative */}
                  <div className="mt-6 p-4 bg-gray-50 rounded-lg">
                    <p className="text-sm text-gray-600">
                      <span className="font-medium">Alternative:</span> Pay ${billingConfig.endpoint_price_monthly}/endpoint/month for usage-based pricing that scales automatically.
                    </p>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* Payment Methods */}
      {billingStatus?.payment_methods && billingStatus.payment_methods.length > 0 && (
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-gray-900">Payment Methods</h3>
            <button
              onClick={handleManageBilling}
              className="text-sm text-indigo-600 hover:text-indigo-800 font-medium"
            >
              Manage
            </button>
          </div>
          <div className="space-y-3">
            {billingStatus.payment_methods.map((method) => (
              <div
                key={method.id}
                className="flex items-center gap-4 p-4 border border-gray-200 rounded-lg"
              >
                <div className="w-12 h-8 bg-gray-100 rounded flex items-center justify-center">
                  <span className="text-xs font-bold text-gray-600 uppercase">{method.brand}</span>
                </div>
                <div className="flex-1">
                  <p className="font-medium text-gray-900">
                    •••• •••• •••• {method.last4}
                  </p>
                  <p className="text-sm text-gray-500">
                    Expires {method.exp_month}/{method.exp_year}
                  </p>
                </div>
                {method.is_default && (
                  <span className="px-2 py-1 text-xs font-medium text-green-700 bg-green-100 rounded">
                    Default
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Invoices */}
      {invoices.length > 0 && (
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Invoice History</h3>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Invoice</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Date</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Amount</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {invoices.map((invoice) => (
                  <tr key={invoice.id} className="hover:bg-indigo-50/50">
                    <td className="px-4 py-3">
                      <span className="font-mono text-sm">{invoice.number}</span>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {formatDate(invoice.created)}
                    </td>
                    <td className="px-4 py-3 text-sm font-medium text-gray-900">
                      {formatCurrency(invoice.amount_paid || invoice.amount_due, invoice.currency)}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-1 text-xs font-medium rounded-full ${
                        invoice.status === 'paid'
                          ? 'bg-green-100 text-green-800'
                          : invoice.status === 'open'
                          ? 'bg-yellow-100 text-yellow-800'
                          : 'bg-gray-100 text-gray-800'
                      }`}>
                        {invoice.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        {invoice.hosted_invoice_url && (
                          <a
                            href={invoice.hosted_invoice_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-sm text-indigo-600 hover:text-indigo-800"
                          >
                            View
                          </a>
                        )}
                        {invoice.invoice_pdf && (
                          <a
                            href={invoice.invoice_pdf}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-sm text-gray-600 hover:text-gray-800"
                          >
                            PDF
                          </a>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

export default PartnerBilling;
