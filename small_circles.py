import abc
from inspect import isclass
from math import acos, asin, atan2, cos, degrees, pi, radians, sin, sqrt

import numpy as np
from matplotlib import patheffects
from matplotlib.collections import LineCollection
from matplotlib.mlab import griddata
from matplotlib.patches import Circle

# http://treyhunner.com/2016/02/how-to-merge-dictionaries-in-python/
try:
    from collections import ChainMap
except ImportError:
    from itertools import chain

    def ChainMap(*args):
        return dict(chain(*map(lambda d: d.items(), reversed(args))))


def dcos_line(trend_plunge):
    tr, pl = np.transpose(np.radians(trend_plunge))  # trend, plunge
    return np.array(
        (np.cos(pl) * np.sin(tr), np.cos(pl) * np.cos(tr), -np.sin(pl))
    ).T


def sphere_line(dcos_data):
    x, y, z = np.transpose(dcos_data)
    sign_z = np.where(z > 0, -1, 1)
    z = np.clip(z, -1.0, 1.0)
    return np.array(
        (
            np.degrees(np.arctan2(sign_z * x, sign_z * y)) % 360,
            np.degrees(np.arcsin(np.abs(z))),
        )
    ).T


def normalized_cross(a, b):
    c = np.cross(a, b)
    length = sqrt(c.dot(c))
    return c / length if length > 0 else c


def build_rotation_matrix(azim, plng, rake):
    azim, plng, rake = radians(azim), radians(plng), radians(rake)

    R1 = np.array(
        (
            (cos(rake), 0.0, sin(rake)),
            (0.0, 1.0, 0.0),
            (-sin(rake), 0.0, cos(rake)),
        )
    )

    R2 = np.array(
        (
            (1.0, 0.0, 0.0),
            (0.0, cos(plng), sin(plng)),
            (0.0, -sin(plng), cos(plng)),
        )
    )

    R3 = np.array(
        (
            (cos(azim), sin(azim), 0.0),
            (-sin(azim), cos(azim), 0.0),
            (0.0, 0.0, 1.0),
        )
    )

    return R3.dot(R2).dot(R1)


def fit_girdle(data):
    direction_tensor = np.dot(np.transpose(data), data)
    eigenvalues, eigenvectors = np.linalg.eigh(direction_tensor)
    axis = Vector(eigenvectors[:, eigenvalues.argmin()])
    return axis/axis.length


def fit_small_circle(data):
    eigenvalues, eigenvectors = np.linalg.eigh(np.cov(data, rowvar=False))
    axis = Vector(eigenvectors[:, eigenvalues.argmin()])
    return axis/axis.length


class Vector(np.ndarray):
    def __new__(cls, dcos_data):
        return np.asarray(dcos_data).view(cls)

    def angle_with(self, other, precise=False):
        if not precise:
            self_length = self.length
            other_length = sqrt(other.dot(other))
            return acos(np.clip(self.dot(other) / (self_length * other_length), -1, 1))
        else:
            return atan2(self.cross_with(other), self.dot(other))

    def cross_with(self, other):
        return Vector(np.cross(self, other))

    def normalized_cross_with(self, other):
        return Vector(normalized_cross(self, other))

    @property
    def attitude(self):
        x, y, z = self / self.length
        if z > 0:
            x, y = -x, -y
        return degrees(atan2(x, y)) % 360, degrees(asin(abs(z)))

    @property  # this should be cached
    def length(self):
        return sqrt(self.dot(self))

    @property
    def direction_vector(self):
        if abs(self[2]) == 1.0:
            return Vector((1.0, 0.0, 0.0))
        direction = Vector((self[1], -self[0], 0.0))
        return direction / direction.length

    @property
    def dip_vector(self):
        return Vector(np.cross(self / self.length, self.direction_vector))

    @property
    def projection_matrix(self):
        return np.outer(self, self)

    @property
    def rejection_matrix(self):
        return np.eye(3) - self.projection_matrix

    @property
    def cross_product_matrix(self):
        return np.array(
            (
                (0.0, -self[2], self[1]),
                (self[2], 0.0, -self[0]),
                (-self[1], self[0], 0.0),
            )
        )

    def get_rotation_matrix(self, theta):
        return (
            cos(theta) * np.eye(3)
            + sin(theta) * self.cross_product_matrix
            + (1 - cos(theta)) * self.projection_matrix
        )

    def get_great_circle(self, step=radians(1.0), offset=0.0):
        theta_range = np.arange(offset, 2 * pi + offset, step) % (2 * pi)
        sin_range = np.sin(theta_range)
        cos_range = np.cos(theta_range)
        return (
            (
                self.direction_vector[:, None] * cos_range
                + self.dip_vector[:, None] * sin_range
            ).T,
        )

    def get_small_circle(self, alpha, A=0, B=0, step=radians(1.0), offset=0.0):
        if A == 0 and B == 0:
            sc = self.get_great_circle(step, offset)[0].T * sin(alpha) + self[
                :, None
            ] * cos(alpha)
        else:
            theta_range = np.arange(0, 2 * pi, step)
            alpha_ = (
                alpha
                + A * np.cos(2 * theta_range)
                + B * np.sin(2 * theta_range)
            )
            sc = self.get_great_circle(step)[0].T * np.sin(alpha_) + self[
                :, None
            ] * np.cos(alpha_)
        return sc.T, -sc.T

    def arc_to(self, other, step=radians(1.0)):
        normal = self.rejection_matrix.dot(other)
        normal /= sqrt(normal.dot(normal))
        theta_range = np.arange(0, self.angle_with(other), step)
        sin_range = np.sin(theta_range)
        cos_range = np.cos(theta_range)
        return ((self * cos_range[:, None] + normal * sin_range[:, None]),)

    @staticmethod
    def from_attitude(trend, plunge):
        return Vector(dcos_line((trend, plunge)))


class VectorSet(np.ndarray):
    """Class that represents a set (collection) of Vectors.

    Parameters:
        dcos_data: Is an array of direction cosines.
    """

    item_class = Vector

    def __new__(cls, dcos_data):
        obj = np.asarray(dcos_data).view(cls)
        return obj

    def __finalize_array__(self, obj):
        if obj is None:
            return

    def __getitem__(self, x):
        item = super(VectorSet, self).__getitem__(x)
        if np.atleast_2d(item).shape == (1, 3):
            return item.view(self.item_class)
        else:
            return item

    # @property
    # def stats(self):
    #     """Contains spherical statistics object for the data
    #     set.
    #     """
    #     return SphericalStatistics(self)

    @property
    def attitude(self):
        """Converts this data from direction cosines to attitudes."""
        return sphere_line(self)

    # def count_fisher(self, k=None, grid=None):
    #     """Performs grid counting of the data by Fisher smoothing.

    #     Parameters:
    #         k: von Mises-Fisher k parameter, see 
    #         stats.SphericalGrid.count_fisher.
            
    #         grid: A stats.Spherical grid object to count on. If None
    #         the default grid defined on stats.DEFAULT_GRID will be
    #         used.
    #     """
    #     if grid is None:
    #         grid = DEFAULT_GRID
    #     return grid.count_fisher(self, k)

    # def count_kamb(self, theta=None, grid=None):
    #     """Performs grid counting of the data by small circles of
    #     aperture theta.

    #     Parameters:
    #         theta: Robin and Jowett (1986) based on Kamb (1956) theta
    #         parameter, see stats.SphericalGrid.count_kamb.
            
    #         grid: A stats.Spherical grid object to count on. If None
    #         the default grid defined on stats.DEFAULT_GRID will be
    #         used.
    #     """
    #     if grid is None:
    #         grid = DEFAULT_GRID
    #     return grid.count_kamb(self, theta)

    def normalized_cross_with(self, other):
        """Returns a VectorSet object containing the normalized cross
        product of all possible pairs between this VectorSet and an
        (n, 3) array-like

        Parameter:
            other: A VectorSet like object.
        """
        vectors = np.zeros((len(self) * len(other), 3))
        i = 0
        for self_vector in self:
            for other_vector in other:
                cross = normalized_cross(self_vector, other_vector)
                vectors[i] = cross if cross[2] < 0 else -cross
                i += 1
        return VectorSet(vectors)

    def angle_with(self, other, precise=False):
        """Returns the angles matrix between this Spherical Data and an
        (n, 3) array-like.

        Parameter:
            other: A VectorSet like object.
            precise: whether to use arccosine or arctangent (defaults False)
        """
        angles = np.zeros((len(self), len(other)))
        for i, self_vector in enumerate(self):
            for j, other_vector in enumerate(other):
                angles[i, j] = self_vector.angle_with(other_vector, precise)
        return angles

    def get_great_circle(self, step=radians(1.0)):
        """Returns a generator to the list of great circles of 
        this VectorSet vectors.

        Parameters:
            step: Angular step in radians to generate points around great 
            circle.
        """
        for vector in self:
            yield vector.get_great_circle(step)[0]  # because of plot_circles


class ProjectionBase(object):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def _dtr(self, x, y, z):
        raise Exception()

    @abc.abstractmethod
    def _itr(self, X, Y):
        raise Exception()

    def __init__(self, rotation=None):
        # Maybe check if rotation is already a rotation matrix, and
        # sets it directly if so. Then a matrix generated by a
        # Vector could be used here.
        self.rotation = rotation
        if rotation is not None:
            self.R = build_rotation_matrix(*rotation)
            self.I = np.linalg.inv(self.R)
        else:
            self.R = self.I = np.eye(3)

    def _pre_direct(self, data, invert_positive, rotate):
        if rotate and self.rotation is not None:
            x, y, z = self.R.dot(np.transpose(data))
        else:
            x, y, z = np.transpose(data)
        d = 1.0 / np.sqrt(x * x + y * y + z * z)
        if invert_positive:
            c = np.where(z > 0, -1, 1) * d
            return c * x, c * y, c * z
        else:
            return d * x, d * y, d * z

    def _post_inverse(self, data, rotate):
        if rotate and self.rotation is not None:
            return np.transpose(self.I.dot(data))
        else:
            return np.transpose(data)

    def direct(self, data, invert_positive=True, rotate=True):
        return self._dtr(*self._pre_direct(data, invert_positive, rotate))

    def inverse(self, data, rotate=True):
        return self._post_inverse(self._itr(*np.transpose(data)), rotate)


class EqualAngle(ProjectionBase):
    def _dtr(self, x, y, z):
        """equal-angle (stereographic) projection.

        Projects a point from the unit sphere to a plane using
        stereographic projection"""
        return x / (1 - z), y / (1 - z)

    def _itr(self, X, Y):
        """inverse equal-angle (stereographic) projection.

        Inverts the projection of a point from the unit sphere
        to a plane using stereographic projection"""
        x = 2.0 * X / (1.0 + X * X + Y * Y)
        y = 2.0 * Y / (1.0 + X * X + Y * Y)
        z = (-1.0 + X * X + Y * Y) / (1.0 + X * X + Y * Y)
        return x, y, z


class Orthographic(ProjectionBase):
    def _dtr(self, x, y, z):
        """orthographic projection on z=0 plane."""
        return x, y

    def _itr(self, X, Y):
        """Inverse orthographic projection from z=0 plane to unit sphere."""
        x, y = X, Y
        z = np.sqrt(1 - x * x - y * y)
        return x, y, z


class EqualArea(ProjectionBase):
    def _dtr(self, x, y, z):
        """equal-area (schmidt-lambert) projection.

        Projects a point from the unit sphere to a plane using
        lambert equal-area projection, though shrinking the projected
        sphere radius to 1 from sqrt(2)."""
        return x * np.sqrt(1 / (1 - z)), y * np.sqrt(1 / (1 - z))

    def _itr(self, X, Y):
        """inverse equal-area (schmidt-lambert) projection.

        Inverts the projection of a point from the unit sphere
        to a plane using lambert equal-area projection, cosidering
        that the projected radius of the sphere was shrunk to 1 from
        sqrt(2)."""
        X, Y = X * sqrt(2), Y * sqrt(2)
        x = np.sqrt(1 - (X * X + Y * Y) / 4.0) * X
        y = np.sqrt(1 - (X * X + Y * Y) / 4.0) * Y
        z = -1.0 + (X * X + Y * Y) / 2
        return x, y, z


class ProjectionPlot(object):
    point_defaults = {"marker": "o", "c": "#000000", "ms": 3.0}

    line_defaults = {"linewidths": 0.8, "colors": "#4D4D4D", "linestyles": "-"}

    polygon_defaults = {
        "linewidths": 0.8,
        "edgecolors": "#4D4D4D",
        "facecolors": "#FF8000",
    }

    contour_defaults = {"cmap": "Reds", "linestyles": "-", "antialiased": True}

    arrow_defaults = {"lw": 1.0, "ls": "-"}

    net_gc_defaults = {
        "linewidths": 0.25,
        "colors": "#808080",
        "linestyles": "-",
    }

    net_sc_defaults = {
        "linewidths": 0.25,
        "colors": "#808080",
        "linestyles": "-",
    }

    text_defaults = {
        "family": "sans-serif",
        "size": "x-small",
        "horizontalalignment": "center",
    }

    @staticmethod
    def _clip_lines(data, z_tol=0.1):
        """segment point pairs between inside and outside of primitive, for
        avoiding spurious lines when plotting circles."""
        z = np.transpose(data)[2]
        inside = z < z_tol
        results = []
        current = []
        for i, is_inside in enumerate(inside):
            if is_inside:
                current.append(data[i])
            elif current:
                results.append(current)
                current = []
        if current:
            results.append(current)
        return results

    @staticmethod
    def _join_segments(segments, c_tol=radians(1.0)):
        """segment point pairs between inside and outside of primitive, for
        avoiding spurious lines when plotting circles."""
        all_joined = False
        while not all_joined and len(segments) > 1:
            all_joined = True
            segment = segments.pop(0)
            if abs(segment[-1].angle_with(segments[0][0])) < c_tol:
                segment.extend(segments.pop(0))
                all_joined = False
            elif abs(segment[0].angle_with(segments[0][-1])) < c_tol:
                segment_b = segments.pop(0)
                segment_b.extend(segment)
                segment = segment_b
                all_joined = False
            elif abs(segment[-1].angle_with(segments[0][-1])) < c_tol:
                segment.extend(reversed(segments.pop(0)))
                all_joined = False
            elif abs(segment[0].angle_with(segments[0][0])) < c_tol:
                segment_b = segments.pop(0)
                segment_b.extend(reversed(segment))
                segment = segment_b
                all_joined = False
            segments.append(segment)
        return segments

    # @staticmethod
    # def _close_polygon(projected_polygon):
    #     print(projected_polygon.shape)
    #     first = projected_polygon[0]
    #     last = projected_polygon[-1]
    #     mid = (first + last) / 2
    #     mid = mid / np.linalg.norm(mid)
    #     if np.dot(first, last) == 0.0:
    #         mid = np.array([first[1], -first[0]])
    #     if np.linalg.norm(first) > 1.0 and np.linalg.norm(last) > 1.0:
    #         return np.vstack(
    #             [projected_polygon, [2 * last, 3 * mid, 2 * first]]
    #         )
    #     return projected_polygon

    @staticmethod
    def _net_grid(gc_spacing=10.0, sc_spacing=10.0, n=360, clean_caps=True):
        theta = np.linspace(0.0, 2 * pi, n)
        gc_spacing, sc_spacing = radians(gc_spacing), radians(sc_spacing)
        if clean_caps:
            theta_gc = np.linspace(0.0 + sc_spacing, pi - sc_spacing, n)
        else:
            theta_gc = np.linspace(0.0, pi, n)
        gc_range = np.arange(0.0, pi + gc_spacing, gc_spacing)
        gc_range = np.hstack((gc_range, -gc_range))
        sc_range = np.arange(0.0, pi + sc_spacing, sc_spacing)
        i, j, k = np.eye(3)
        ik_circle = i[:, None] * np.sin(theta) + k[:, None] * np.cos(theta)
        great_circles = [
            (
                np.array((cos(alpha), 0.0, -sin(alpha)))[:, None]
                * np.sin(theta_gc)
                + j[:, None] * np.cos(theta_gc)
            ).T
            for alpha in gc_range
        ]
        small_circles = [
            (ik_circle * sin(alpha) + j[:, None] * cos(alpha)).T
            for alpha in sc_range
        ]
        if clean_caps:
            for cap in (0, pi):
                theta_gc = np.linspace(cap - sc_spacing, cap + sc_spacing, n)
                great_circles += [
                    (
                        np.array((cos(alpha), 0.0, -sin(alpha)))[:, None]
                        * np.sin(theta_gc)
                        + j[:, None] * np.cos(theta_gc)
                    ).T
                    for alpha in (0, pi / 2.0)
                ]
        return great_circles, small_circles

    def __init__(self, axis=None, projection=None, rotation=None):
        if projection is None:
            self.projection = EqualArea(rotation)
        elif isclass(projection):
            self.projection = projection(rotation)
        else:
            self.projection = projection
        if axis is None:
            from matplotlib import pyplot as plt

            self.axis = plt.gca()
            self.clear_diagram()
        else:
            self.axis = axis
            self.clear_diagram()

    def clear_diagram(self):
        """Clears the plot area and plot the primitive."""
        self.axis.cla()
        self.axis.axis("equal")
        self.axis.set_xlim(-1.1, 1.1)
        self.axis.set_ylim(-1.1, 1.1)
        self.axis.set_axis_off()
        self.plot_primitive()

    def plot_primitive(self):
        """Plots the primitive, center, NESW indicators and North if no
        rotation."""
        self.primitive = Circle(
            (0, 0),
            radius=1,
            edgecolor="black",
            fill=False,
            clip_box="None",
            label="_nolegend_",
        )
        self.axis.add_patch(self.primitive)
        # maybe add a dict for font options and such...
        if self.projection.rotation is None:
            self.axis.text(0.01, 1.025, "N", **self.text_defaults)
            x_cross = [0, 1, 0, -1, 0]
            y_cross = [0, 0, 1, 0, -1]
            self.axis.plot(
                x_cross, y_cross, "k+", markersize=8, label="_nolegend_"
            )

    def as_points(self, vectors, **kwargs):
        """Plot points on the diagram. Accepts and passes aditional key word
        arguments to axis.plot."""
        X, Y = self.projection.direct(vectors)
        # use the default values if not user input
        # https://stackoverflow.com/a/6354485/1457481
        options = ChainMap({}, kwargs, self.point_defaults)
        self.axis.plot(X, Y, linestyle="", **options)

    def as_lines(self, lines, **kwargs):
        """plot a list of lines"""
        # use the default values if not user input
        # https://stackoverflow.com/a/6354485/1457481
        options = ChainMap({}, kwargs, self.line_defaults)
        # should change this for better support of huge data
        projected_lines = [
            np.transpose(
                self.projection.direct(
                    segment, invert_positive=False, rotate=False
                )
            )
            for circle in lines
            for segment in self._join_segments(
                self._clip_lines(np.dot(VectorSet(circle), self.projection.R.T))
            )
        ]
        circle_collection = LineCollection(projected_lines, **options)
        circle_collection.set_clip_path(self.primitive)
        self.axis.add_collection(circle_collection)

    def as_contours(
        self,
        nodes,
        count,
        n_data,
        n_contours=10,
        minmax=True,
        percentage=True,
        contour_mode="fillover",
        resolution=250,
        **kwargs
    ):
        """Plot contours of a spherical count. Parameters are the counting
        nodes, the actual counts and the number of data points. Returns the
        matplotlib contour object for creating colorbar."""
        if percentage:
            count = 100.0 * count / n_data
        if minmax:
            intervals = np.linspace(count.min(), count.max(), n_contours)
        else:
            intervals = np.linspace(0, count.max(), n_contours)
        xi = yi = np.linspace(-1.1, 1.1, resolution)
        # maybe preselect nodes here on z tolerance
        X, Y = self.projection.direct(nodes, invert_positive=False)
        zi = griddata(X, Y, count, xi, yi, interp="linear")
        # use the default values if not user input
        # https://stackoverflow.com/a/6354485/1457481
        options = ChainMap({}, kwargs, self.contour_defaults)

        contour_fill, contour_lines = None, None
        if contour_mode in ("fillover", "fill"):
            contour_fill = self.axis.contourf(xi, yi, zi, intervals, **options)
            for collection in contour_fill.collections:
                collection.set_clip_path(self.primitive)
        if contour_mode != "fill":
            contour_lines = self.axis.contour(xi, yi, zi, intervals, **options)
            for collection in contour_lines.collections:
                collection.set_clip_path(self.primitive)

        return contour_fill if contour_fill is not None else contour_lines

    def text(self, vector, text, border=None, **kwargs):
        foreground = kwargs.pop("foreground", "w")
        options = ChainMap({}, kwargs, self.text_defaults)
        X, Y = self.projection.direct(vector)
        txt = self.axis.text(X, Y, text, **options)
        if border is not None:
            txt.set_path_effects(
                [
                    patheffects.withStroke(
                        linewidth=border, foreground=foreground
                    )
                ]
            )

    def base_net(
        self,
        gc_spacing=10.0,
        sc_spacing=10.0,
        n=360,
        gc_options=None,
        sc_options=None,
        clean_caps=True,
        plot_cardinal_points=True,
        cardinal_options=None,
    ):
        gc, sc = self._net_grid(gc_spacing, sc_spacing, n, clean_caps)
        gc_options = {} if gc_options is None else gc_options
        sc_options = {} if sc_options is None else sc_options
        cardinal_options = {} if cardinal_options is None else cardinal_options
        gc_options = ChainMap({}, gc_options, self.net_gc_defaults)
        self.as_lines(gc, **gc_options)
        sc_options = ChainMap({}, sc_options, self.net_sc_defaults)
        self.as_lines(sc, **sc_options)

        cardinal_options = ChainMap(
            {}, cardinal_options, {"verticalalignment": "center"}
        )
        if plot_cardinal_points and self.projection.rotation is not None:
            cpoints = np.array(
                (
                    (0.0, 1.0, 0.0),
                    (1.0, 0.0, 0.0),
                    (0.0, -1.0, 0.0),
                    (-1.0, 0.0, 0.0),
                )
            )
            c_rotated = np.dot(cpoints, self.projection.R.T)
            for i, (point, name) in enumerate(zip(c_rotated, "NESW")):
                if point[2] > 0:
                    continue
                self.text(
                    cpoints[i],
                    name,
                    border=2.0,
                    foreground="w",
                    **cardinal_options
                )


def sample_fisher(mean_vector, kappa, n):
    """Samples n vectors from von Mises-Fisher distribution."""
    mean_vector = Vector(mean_vector)
    direction_vector = mean_vector.direction_vector
    dip_vector = mean_vector.dip_vector
    kappa = kappa
    theta_sample = np.random.uniform(0, 2 * pi, n)
    alpha_sample = np.random.vonmises(0, kappa / 2.0, n)  # Why?
    return VectorSet(
        (
            (
                direction_vector[:, None] * np.cos(theta_sample)
                + dip_vector[:, None] * np.sin(theta_sample)
            )
            * np.sin(alpha_sample)
            + mean_vector[:, None] * np.cos(alpha_sample)
        ).T
    )

def sample_uniform(n):
    """Sample n vectors for the uniform distribution on the sphere."""
    samples = np.random.normal(size=(n, 3))
    return VectorSet(
        samples / np.linalg.norm(samples, axis=1)[:, None])
