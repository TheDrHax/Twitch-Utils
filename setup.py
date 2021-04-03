#!/usr/bin/env python3

import itertools
from setuptools import setup, find_packages


with open('README.md', 'r') as fi:
    long_description = fi.read()


EXTRAS = {
    'record': [
        'streamlink>=1.0.0',
        'parse>=1.19.0'
    ],
    'offset': [
        'praat-parselmouth>=0.3.3'
    ],
    'mute': [
        'numpy<1.19.0,>=1.16.0',
        'tensorflow==2.3.0',
        'spleeter>=2.0'
    ]
}

EXTRAS['all'] = list(itertools.chain.from_iterable(EXTRAS.values()))


setup(
    name='tdh-twitch-utils',
    version='1.5',

    author='Dmitry Karikh',
    author_email='the.dr.hax@gmail.com',

    description='Record, concatenate and synchronize Twitch live streams',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/TheDrHax/Twitch-Utils',

    install_requires=[
        'requests',
        'python-dateutil',
        'docopt>=0.6.2',
    ],

    extras_require=EXTRAS,

    packages=find_packages(),

    entry_points='''
    [console_scripts]
    twitch_utils=twitch_utils:main
    ''',

    classifiers=[
        'Environment :: Console',
        'Programming Language :: Python :: 3.6',
        'Intended Audience :: End Users/Desktop',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Topic :: Multimedia :: Sound/Audio :: Analysis',
        'Topic :: Multimedia :: Video :: Conversion',
        'Topic :: Utilities'
    ]
)
