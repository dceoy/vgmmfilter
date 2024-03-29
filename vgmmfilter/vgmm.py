#!/usr/bin/env python
"""GMM-based Infrequent Variant Filter for VCF data
https://github.com/dceoy/vgmmfilter
"""

import logging
import operator
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.pylab import rcParams
from sklearn.mixture import GaussianMixture


class VariantGMMFilter(object):
    def __init__(self, af_cutoff=0.02, min_salvaged_af=0.2,
                 alpha_of_mvalue=1, target_filtered_variants=None,
                 filter_label='VGMM', min_sample_size=3, peakout_iter=5,
                 model_iter=10, font_family=None, **kwargs):
        self.__logger = logging.getLogger(__name__)
        self.__af_cutoff = af_cutoff
        assert (not min_salvaged_af) or (min_salvaged_af > af_cutoff)
        self.__min_salvaged_af = min_salvaged_af or 1
        self.__mv_alpha = alpha_of_mvalue
        if not target_filtered_variants:
            self.__target_filtered_variants = None
        elif isinstance(target_filtered_variants, str):
            self.__target_filtered_variants = {target_filtered_variants}
        else:
            assert isinstance(target_filtered_variants, (list, tuple, set))
            self.__target_filtered_variants = set(target_filtered_variants)
        self.__filter_label = filter_label
        self.__min_sample_size = min_sample_size
        self.__peakout_iter = peakout_iter
        self.__model_iter = model_iter
        self.__gm_args = kwargs
        self.__font_family = font_family

    def run(self, vcfdf, out_fig_pdf_path=None):
        self._validate_df_vcf(df=vcfdf.df)
        df_vcf = (
            vcfdf.df.pipe(
                lambda d: d[d['FILTER'].isin(self.__target_filtered_variants)]
            ) if self.__target_filtered_variants else vcfdf.df
        ).drop_duplicates()
        sample_size = df_vcf.shape[0]
        self.__logger.debug('sample_size: {}'.format(sample_size))
        if sample_size:
            df_xvcf = vcfdf.expanded_df(
                df=df_vcf, by_info=True, by_samples=False, drop=False
            )
            if sample_size >= self.__min_sample_size:
                df_cl = self._cluster_variants(df_xvcf=df_xvcf)
            else:
                df_cl = df_xvcf.assign(
                    AF=lambda d: d['INFO_AF'].astype(float),
                    DP=lambda d: d['INFO_DP'].astype(int),
                    INDELLEN=lambda d:
                    (d['ALT'].apply(len) - d['REF'].apply(len))
                ).assign(
                    ALTDP=lambda d: (d['AF'] * d['DP'])
                ).assign(
                    CL_AF=lambda d: d['AF'],
                    CL_DP=lambda d: d['DP'],
                    CL_ALTDP=lambda d: d['ALTDP']
                ).assign(
                    dropped=lambda d: (d['CL_AF'] < self.__af_cutoff)
                )
            vcf_cols = vcfdf.df.columns.tolist()
            vcfdf.df = vcfdf.df.merge(
                df_cl[[*vcf_cols, 'dropped']], on=vcf_cols, how='left'
            ).assign(
                dropped=lambda d: d['dropped'].fillna(False)
            ).assign(
                FILTER=lambda d: d['FILTER'].mask(
                    d['dropped'],
                    np.where(
                        d['FILTER'] == 'PASS', self.__filter_label,
                        d['FILTER'] + ';' + self.__filter_label
                    )
                )
            )[vcf_cols]
            self.__logger.info(
                'VariantGMMFilter filtered out variants: {0} / {1}'.format(
                    df_cl['dropped'].sum(), df_vcf.shape[0]
                )
            )
            if out_fig_pdf_path:
                self._draw_fig(df=df_cl, out_fig_path=out_fig_pdf_path)
            else:
                pass
        else:
            self.__logger.info(
                'No variant targeted for {}.'.format(self.__filter_label)
            )
        return vcfdf

    @staticmethod
    def _validate_df_vcf(df):
        if df.size:
            ra = df[['REF', 'ALT']].apply(lambda r: ''.join(r), axis=1)
            if ra[ra.str.contains(',')].size:
                raise ValueError('multiple allele pattern is not supported.')
            elif ra[ra.str.contains(r'[^a-zA-Z]')].size:
                raise ValueError('invalid allele pattern')

    def _cluster_variants(self, df_xvcf):
        n_variants = df_xvcf.shape[0]
        df_x = df_xvcf.assign(
            AF=lambda d: d['INFO_AF'].astype(float),
            DP=lambda d: d['INFO_DP'].astype(int),
            INDELLEN=lambda d: (d['ALT'].apply(len) - d['REF'].apply(len))
        ).assign(
            ALTDP=lambda d: (d['AF'] * d['DP'])
        ).assign(
            M_AF=lambda d: self._af2mvalue(
                af=d['AF'], altdp=d['ALTDP'], alpha=self.__mv_alpha
            ),
            LOG2_ALTDP=lambda d: np.log2(d['ALTDP'])
        )
        self.__logger.debug('df_x:{0}{1}'.format(os.linesep, df_x))
        rvn = ReversibleNormalizer(df=df_x, columns=['M_AF', 'LOG2_ALTDP'])
        best_gmm_dict = None
        for k in range(2, (n_variants + 1)):
            gmm_dict = self._perform_gmm(rvn=rvn, k=k)
            if (gmm_dict['max_dropped_af'] < self.__min_salvaged_af
                    and (not best_gmm_dict
                         or gmm_dict['bic'] < best_gmm_dict['bic'])):
                best_gmm_dict = gmm_dict
            elif (best_gmm_dict
                  and k >= (best_gmm_dict['k'] + self.__peakout_iter)):
                break
        assert bool(best_gmm_dict)
        self.__logger.debug(
            'the best model:{0}{1}{0}{2}'.format(
                os.linesep, best_gmm_dict['gmm'], best_gmm_dict['df_gm_mu']
            )
        )
        return best_gmm_dict['df_variants']

    def _perform_gmm(self, rvn, k):
        x_train = rvn.normalized_df[rvn.columns]
        best_model_of_k = sorted(
            [
                self._gm_fit(x=x_train, n_components=k, **self.__gm_args)
                for _ in range(self.__model_iter)
            ],
            key=operator.itemgetter('bic')
        )[0]
        df_gm_mu = rvn.denormalize(
            df=pd.DataFrame(best_model_of_k['gmm'].means_, columns=rvn.columns)
        ).reset_index().rename(
            columns={
                'index': 'CL_INT', 'M_AF': 'CL_M_AF',
                'LOG2_ALTDP': 'CL_LOG2_ALTDP'
            }
        ).assign(
            CL_ALTDP=lambda d: np.exp2(d['CL_LOG2_ALTDP'])
        ).assign(
            CL_AF=lambda d: self._mvalue2af(
                mvalue=d['CL_M_AF'], altdp=d['CL_ALTDP'], alpha=self.__mv_alpha
            )
        ).assign(
            CL_DP=lambda d: np.divide(d['CL_ALTDP'], d['CL_AF'])
        )
        df_variants = pd.merge(
            rvn.df.assign(CL_INT=best_model_of_k['gmm'].predict(X=x_train)),
            df_gm_mu[['CL_INT', 'CL_AF', 'CL_ALTDP', 'CL_DP']],
            on='CL_INT', how='left'
        ).assign(
            dropped=lambda d: (d['CL_AF'] < self.__af_cutoff)
        )
        max_dropped_af = df_variants.pipe(
            lambda d:
            (d[d['dropped']]['AF'].max() if d['dropped'].any() else 0)
        )
        self.__logger.debug(
            'k: {0}, bic: {1}, max_dropped_af: {2}'.format(
                k, best_model_of_k['bic'], max_dropped_af
            )
        )
        return {
            'k': k, 'bic': best_model_of_k['bic'],
            'gmm': best_model_of_k['gmm'], 'df_gm_mu': df_gm_mu,
            'df_variants': df_variants, 'max_dropped_af': max_dropped_af
        }

    @staticmethod
    def _gm_fit(x, **kwargs):
        gmm = GaussianMixture(**kwargs)
        gmm.fit(X=x)
        return {'bic': gmm.bic(X=x), 'gmm': gmm}

    @staticmethod
    def _af2mvalue(af, altdp, alpha):
        return np.log2(
            np.divide((altdp + alpha), (np.divide(altdp, af) - altdp + alpha))
        )

    @staticmethod
    def _mvalue2af(mvalue, altdp, alpha):
        return (
            lambda x:
            np.divide((x * altdp), ((1 + x) * altdp + (1 - x) * alpha))
        )(x=np.exp2(mvalue))

    def _draw_fig(self, df, out_fig_path):
        self.__logger.info('Draw a fig: {}'.format(out_fig_path))
        if self.__font_family:
            rcParams['font.family'] = self.__font_family
        rcParams['figure.figsize'] = (11.88, 8.40)  # A4 aspect: (297x210)
        sns.set(style='ticks', color_codes=True)
        sns.set_context('paper')
        df_fig = df.sort_values(
            'CL_AF', ascending=False
        ).assign(
            CL=lambda d: d[['CL_ALTDP', 'CL_DP', 'CL_AF']].apply(
                lambda r: '[{0:.1f}/{1:.1f}, {2:.4f}]'.format(*r), axis=1
            ),
            VT=lambda d: np.where(
                d['INDELLEN'] > 0, 'Insertion',
                np.where(d['INDELLEN'] < 0, 'Deletion', 'Substitution')
            )
        )
        cl_labels = {
            k: '{0}\t(x{1})'.format(k, v)
            for k, v in df_fig['CL'].value_counts().to_dict().items()
        }
        vt_labels = {
            k: '{0}\t(x{1})'.format(k, v)
            for k, v in df_fig['VT'].value_counts().to_dict().items()
        }
        fig_lab_names = {
            'AF': 'ALT allele frequency (AF)', 'DP': 'Total read depth (DP)',
            'CL': 'Cluster [ALT/DP, AF]', 'VT': 'Variant Type'
        }
        sns.set_palette(palette='GnBu_d', n_colors=df_fig['CL'].nunique())
        self.__logger.debug('df_fig:{0}{1}'.format(os.linesep, df_fig))
        ax = sns.scatterplot(
            x=fig_lab_names['DP'], y=fig_lab_names['AF'],
            style=fig_lab_names['VT'], hue=fig_lab_names['CL'],
            data=df_fig.assign(
                CL=lambda d: d['CL'].apply(lambda k: cl_labels[k]),
                VT=lambda d: d['VT'].apply(lambda k: vt_labels[k])
            ).rename(columns=fig_lab_names)[fig_lab_names.values()],
            markers={
                vt_labels[k]: v for k, v in {
                    'Substitution': '.', 'Deletion': '>', 'Insertion': '<'
                }.items() if k in vt_labels
            },
            alpha=0.8, edgecolor='none', legend='full'
        )
        ax.set_xscale('log')
        ax.set_title('Variant GMM Clusters')
        ax.legend(loc='upper left', bbox_to_anchor=(1, 1), ncol=1)
        axp = ax.get_position()
        ax.set_position([axp.x0, axp.y0, axp.width * 0.75, axp.height])
        plt.savefig(out_fig_path)


class ReversibleNormalizer(object):
    def __init__(self, df, columns=None):
        self.df = df
        self.columns = columns or self.df.columns.tolist()
        self.mean_dict = self.df[self.columns].mean(axis=0).to_dict()
        self.std_dict = self.df[self.columns].std(axis=0).to_dict()
        self.normalized_df = self.normalize(df=self.df)

    def normalize(self, df):
        return df.pipe(
            lambda d: d[[c for c in d.columns if c not in self.columns]]
        ).join(
            pd.DataFrame([
                {
                    'index': id,
                    **{
                        k: np.divide((v - self.mean_dict[k]), self.std_dict[k])
                        for k, v in row.items()
                    }
                } for id, row in df[self.columns].iterrows()
            ]).set_index('index'),
            how='left'
        )[df.columns]

    def denormalize(self, df):
        return df.pipe(
            lambda d: d[[c for c in d.columns if c not in self.columns]]
        ).join(
            pd.DataFrame([
                {
                    'index': id,
                    **{
                        k: ((v * self.std_dict[k]) + self.mean_dict[k])
                        for k, v in row.items()
                    }
                } for id, row in df[self.columns].iterrows()
            ]).set_index('index'),
            how='left'
        )[df.columns]
