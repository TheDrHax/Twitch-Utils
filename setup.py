#!/usr/bin/env python

from setuptools import setup, find_packages


with open('README.md', 'r') as fi:
    long_description = fi.read()


setup(
    name='tdh-twitch-utils',
    version='1.2',

    author='Dmitry Karikh',
    author_email='the.dr.hax@gmail.com',

    description='Record, concatenate and synchronize Twitch live streams',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/TheDrHax/Twitch-Utils',

    install_requires=[
        'tdh-tcd>=2.4',
        'streamlink>=1.0.0',
        'docopt>=0.6.2',
        'praat-parselmouth>=0.3.3'
    ],

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
