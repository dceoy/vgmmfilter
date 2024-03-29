#!/usr/bin/env python

import os

from setuptools import find_packages, setup

from vgmmfilter import __version__

with open(os.path.join(os.path.dirname(__file__), 'README.md'), 'r') as f:
    readme_str = f.read()

setup(
    name='vgmmfilter',
    version=__version__,
    description='GMM-based Infrequent Variant Filter for VCF data',
    packages=find_packages(),
    author='Daichi Narushima',
    author_email='dnarsil+github@gmail.com',
    url='https://github.com/dceoy/vgmmfilter',
    include_package_data=True,
    install_requires=['docopt', 'pandas', 'scikit-learn', 'seaborn'],
    entry_points={
        'console_scripts': ['vgmmfilter=vgmmfilter.cli:main'],
    },
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Plugins',
        'License :: OSI Approved :: MIT License',
        'Operating System :: POSIX :: Linux',
        'Operating System :: MacOS',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Topic :: Scientific/Engineering',
        'Topic :: Scientific/Engineering :: Bio-Informatics'
    ],
    long_description=readme_str
)
