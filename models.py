import json
from pathlib import Path

import attr
import torch
import torch.cuda
import torch.nn as nn
import torch.nn.functional as F


@attr.s(slots=True)
class HyperParams:
    classes = attr.ib(default=list(range(10)))
    net = attr.ib(default='UNet')
    n_channels = attr.ib(default=12)  # max 20
    total_classes = 10
    thresholds = attr.ib(default=[0.5])

    patch_inner = attr.ib(default=64)
    patch_border = attr.ib(default=16)

    augment_rotations = attr.ib(default=10.0)  # degrees
    augment_flips = attr.ib(default=0)
    augment_channels = attr.ib(default=0)

    validation_square = attr.ib(default=400)

    dropout = attr.ib(default=0.0)
    bn = attr.ib(default=1)
    activation = attr.ib(default='relu')
    dice_loss = attr.ib(default=0.0)
    dist_loss = attr.ib(default=0.0)

    filters_base = attr.ib(default=32)

    n_epochs = attr.ib(default=100)
    oversample = attr.ib(default=0.0)
    lr = attr.ib(default=0.0001)
    lr_decay = attr.ib(default=0.0)
    batch_size = attr.ib(default=128)

    @property
    def n_classes(self):
        return len(self.classes)

    @property
    def has_all_classes(self):
        return self.n_classes == self.total_classes

    @classmethod
    def from_dir(cls, root: Path):
        params = json.loads(root.joinpath('hps.json').read_text())
        fields = {field.name for field in attr.fields(HyperParams)}
        return cls(**{k: v for k, v in params.items() if k in fields})

    def update(self, hps_string: str):
        if hps_string:
            values = dict(pair.split('=') for pair in hps_string.split(','))
            for field in attr.fields(HyperParams):
                v = values.pop(field.name, None)
                if v is not None:
                    default = field.default
                    assert not isinstance(default, bool)
                    if isinstance(default, (int, float, str)):
                        v = type(default)(v)
                    elif isinstance(default, list):
                        v = [int(x) for x in v.split('-')]
                    setattr(self, field.name, v)
            if values:
                raise ValueError('Unknown hyperparams: {}'.format(values))


class BaseNet(nn.Module):
    def __init__(self, hps: HyperParams):
        super().__init__()
        self.hps = hps
        if hps.dropout:
            self.dropout = nn.Dropout(p=hps.dropout)
            self.dropout2d = nn.Dropout2d(p=hps.dropout)
        else:
            self.dropout = self.dropout2d = lambda x: x
        self.register_buffer('global_step', torch.IntTensor(1).zero_())


def conv3x3(in_, out):
    return nn.Conv2d(in_, out, 3, padding=1)


class MiniNet(BaseNet):
    def __init__(self, hps):
        super().__init__(hps)
        self.conv1 = nn.Conv2d(hps.n_channels, 4, 1)
        self.conv2 = nn.Conv2d(4, 8, 3, padding=1)
        self.conv3 = nn.Conv2d(8, hps.n_classes, 3, padding=1)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = self.conv3(x)
        b = self.hps.patch_border
        return F.sigmoid(x[:, :, b:-b, b:-b])


class OldNet(BaseNet):
    def __init__(self, hps):
        super().__init__(hps)
        self.conv1 = nn.Conv2d(hps.n_channels, 64, 5, padding=2)
        self.conv2 = nn.Conv2d(64, 64, 5, padding=2)
        self.conv3 = nn.Conv2d(64, 64, 5, padding=2)
        self.conv4 = nn.Conv2d(64, hps.n_classes, 7, padding=3)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = self.conv4(x)
        b = self.hps.patch_border
        return F.sigmoid(x[:, :, b:-b, b:-b])


class SmallNet(BaseNet):
    def __init__(self, hps):
        super().__init__(hps)
        self.conv1 = nn.Conv2d(hps.n_channels, 64, 3, padding=1)
        self.conv2 = nn.Conv2d(64, 64, 3, padding=1)
        self.conv3 = nn.Conv2d(64, 64, 3, padding=1)
        self.conv4 = nn.Conv2d(64, 128, 3, padding=1)
        self.conv5 = nn.Conv2d(128, hps.n_classes, 3, padding=1)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = F.relu(self.conv4(x))
        x = self.conv5(x)
        b = self.hps.patch_border
        return F.sigmoid(x[:, :, b:-b, b:-b])


def upsample2d(x):
    # repeat is missing: https://github.com/pytorch/pytorch/issues/440
    # return x.repeat(1, 1, 2, 2)
    x = torch.stack([x[:, :, i // 2, :] for i in range(x.size()[2] * 2)], 2)
    x = torch.stack([x[:, :, :, i // 2] for i in range(x.size()[3] * 2)], 3)
    return x


# UNet:
# http://lmb.informatik.uni-freiburg.de/people/ronneber/u-net/u-net-architecture.png


class SmallUNet(BaseNet):
    def __init__(self, hps):
        super().__init__(hps)
        self.conv1 = nn.Conv2d(hps.n_channels, 32, 3, padding=1)
        self.conv2 = nn.Conv2d(32, 32, 3, padding=1)
        self.pool = nn.MaxPool2d(2, 2)
        self.conv3 = nn.Conv2d(32, 64, 3, padding=1)
        self.conv4 = nn.Conv2d(64, 64, 3, padding=1)
        self.conv5 = nn.Conv2d(64, 32, 3, padding=1)
        self.conv6 = nn.Conv2d(64, 32, 3, padding=1)
        self.conv7 = nn.Conv2d(32, hps.n_classes, 3, padding=1)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x1 = self.pool(x)
        x1 = F.relu(self.conv3(x1))
        x1 = F.relu(self.conv4(x1))
        x1 = F.relu(self.conv5(x1))
        x1 = upsample2d(x1)
        x = torch.cat([x, x1], 1)
        x = F.relu(self.conv6(x))
        x = self.conv7(x)
        b = self.hps.patch_border
        return F.sigmoid(x[:, :, b:-b, b:-b])


class UNetModule(nn.Module):
    def __init__(self, hps: HyperParams, in_: int, out: int):
        super().__init__()
        self.conv1 = conv3x3(in_, out)
        self.conv2 = conv3x3(out, out)
        self.bn = hps.bn
        self.activation = getattr(F, hps.activation)
        if self.bn:
            self.bn1 = nn.BatchNorm2d(out)
            self.bn2 = nn.BatchNorm2d(out)

    def forward(self, x):
        x = self.conv1(x)
        if self.bn:
            x = self.bn1(x)
        x = self.activation(x)
        x = self.conv2(x)
        if self.bn:
            x = self.bn2(x)
        x = self.activation(x)
        return x


class UNet(BaseNet):
    module = UNetModule
    filter_factors = [1, 2, 4, 8, 16]

    def __init__(self, hps: HyperParams):
        super().__init__(hps)
        self.pool = nn.MaxPool2d(2, 2)
        filter_sizes = [hps.filters_base * s for s in self.filter_factors]
        self.down, self.up = [], []
        for i, nf in enumerate(filter_sizes):
            low_nf = hps.n_channels if i == 0 else filter_sizes[i - 1]
            self.down.append(self.module(hps, low_nf, nf))
            setattr(self, 'down_{}'.format(i), self.down[-1])
            if i != 0:
                self.up.append(self.module(hps, low_nf + nf, low_nf))
                setattr(self, 'conv_up_{}'.format(i), self.up[-1])
        self.conv_final = nn.Conv2d(filter_sizes[0], hps.n_classes, 1)

    def forward(self, x):
        xs = []
        for i, down in enumerate(self.down):
            x_out = down(self.pool(xs[-1]) if xs else x)
            x_out = self.dropout2d(x_out)
            xs.append(x_out)

        x_out = xs[-1]
        for x_skip, up in reversed(list(zip(xs[:-1], self.up))):
            x_out = up(torch.cat([upsample2d(x_out), x_skip], 1))
            x_out = self.dropout2d(x_out)

        x_out = self.conv_final(x_out)
        b = self.hps.patch_border
        return F.sigmoid(x_out[:, :, b:-b, b:-b])


class Conv3BN(nn.Module):
    def __init__(self, hps: HyperParams, in_: int, out: int):
        super().__init__()
        self.conv = conv3x3(in_, out)
        self.bn = nn.BatchNorm2d(out) if hps.bn else None
        self.activation = getattr(F, hps.activation)

    def forward(self, x):
        x = self.conv(x)
        if self.bn is not None:
            x = self.bn(x)
        x = self.activation(x, inplace=True)
        return x


class UNet3lModule(nn.Module):
    def __init__(self, hps: HyperParams, in_: int, out: int):
        super().__init__()
        self.l1 = Conv3BN(hps, in_, out)
        self.l2 = Conv3BN(hps, out, out)
        self.l3 = Conv3BN(hps, out, out)

    def forward(self, x):
        x = self.l1(x)
        x = self.l2(x)
        x = self.l3(x)
        return x


class UNet3l(UNet):
    module = UNet3lModule


class UNet2Module(nn.Module):
    def __init__(self, hps: HyperParams, in_: int, out: int):
        super().__init__()
        self.l1 = Conv3BN(hps, in_, out)
        self.l2 = Conv3BN(hps, out, out)

    def forward(self, x):
        x = self.l1(x)
        x = self.l2(x)
        return x


class UNet2(BaseNet):
    def __init__(self, hps):
        super().__init__(hps)
        b = hps.filters_base
        self.filters = [b * 2, b * 2, b * 4, b * 8, b * 16]
        self.down, self.down_pool, self.mid, self.up = [[] for _ in range(4)]
        for i, nf in enumerate(self.filters):
            low_nf = hps.n_channels if i == 0 else self.filters[i - 1]
            self.down_pool.append(
                nn.Conv2d(low_nf, low_nf, 3, padding=1, stride=2))
            setattr(self, 'down_pool_{}'.format(i), self.down_pool[-1])
            self.down.append(UNet2Module(hps, low_nf, nf))
            setattr(self, 'down_{}'.format(i), self.down[-1])
            if i != 0:
                self.mid.append(Conv3BN(hps, low_nf, low_nf))
                setattr(self, 'mid_{}'.format(i), self.mid[-1])
                self.up.append(UNet2Module(hps, low_nf + nf, low_nf))
                setattr(self, 'up_{}'.format(i), self.up[-1])
        self.conv_final = nn.Conv2d(self.filters[0], hps.n_classes, 1)

    def forward(self, x):
        xs = []
        for i, (down, down_pool) in enumerate(zip(self.down, self.down_pool)):
            x_out = down(down_pool(xs[-1]) if xs else x)
            xs.append(x_out)

        x_out = xs[-1]
        for x_skip, up, mid in reversed(list(zip(xs[:-1], self.up, self.mid))):
            x_out = up(torch.cat([upsample2d(x_out), mid(x_skip)], 1))

        x_out = self.conv_final(x_out)
        b = self.hps.patch_border
        return F.sigmoid(x_out[:, :, b:-b, b:-b])


class BasicConv2d(nn.Module):
    def __init__(self, in_planes, out_planes, kernel_size, stride=1, padding=0):
        super().__init__()
        self.conv = nn.Conv2d(
            in_planes, out_planes,
            kernel_size=kernel_size, stride=stride, padding=padding, bias=False)
        self.bn = nn.BatchNorm2d(out_planes, affine=True)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        x = self.conv(x)
        x = self.bn(x)
        x = self.relu(x)
        return x


class InceptionModule(nn.Module):
    def __init__(self, hps: HyperParams, in_: int, out: int):
        super().__init__()
        out_1 = out * 3 // 8
        out_2 = out * 2 // 8
        self.conv1x1 = BasicConv2d(in_, out_1, kernel_size=1)
        self.conv3x3_pre = BasicConv2d(in_, in_ // 2, kernel_size=1)
        self.conv3x3 = BasicConv2d(in_ // 2, out_1, kernel_size=3, padding=1)
        self.conv5x5_pre = BasicConv2d(in_, in_ // 4, kernel_size=1)
        self.conv5x5 = BasicConv2d(in_ // 4, out_2, kernel_size=5, padding=2)
        assert hps.bn
        assert hps.activation == 'relu'

    def forward(self, x):
        return torch.cat([
            self.conv1x1(x),
            self.conv3x3(self.conv3x3_pre(x)),
            self.conv5x5(self.conv5x5_pre(x)),
        ], 1)


class Inception2Module(nn.Module):
    def __init__(self, hps: HyperParams, in_: int, out: int):
        super().__init__()
        self.l1 = InceptionModule(hps, in_, out)
        self.l2 = InceptionModule(hps, out, out)

    def forward(self, x):
        x = self.l1(x)
        x = self.l2(x)
        return x


class InceptionUNet(UNet):
    module = InceptionModule


class Inception2UNet(UNet):
    module = Inception2Module
