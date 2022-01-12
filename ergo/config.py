"""Summary."""
import copy
from collections import OrderedDict
from typing import Dict, Optional
import uuid
from ergo.topic import PubTopic, SubTopic, Topic


class Config:
    """Summary."""

    # pylint: disable=too-many-instance-attributes
    # Eight is reasonable in this case.

    def __init__(self, config: Dict[str, str]):
        """Summary.

        Args:
            config (Dict[str, str]): Description
        """
        self._func: str = config['func']
        self._namespace: Optional[str] = config.get('namespace', 'local')
        self._pubtopic: PubTopic = PubTopic(config.get('pubtopic'))
        self._subtopic: SubTopic = SubTopic(config.get('subtopic'))
        self._host: Optional[str] = config.get('host')
        self._exchange: Optional[str] = config.get('exchange')
        self._protocol: str = config.get('protocol', 'stack')  # http, amqp, stdio, stack
        self._heartbeat: Optional[str] = config.get('heartbeat')
        self._parameters: Optional[OrderedDict] = None

    def copy(self):
        return copy.deepcopy(self)

    @property
    def parameters(self):
        """Summary.

        Returns:
            TYPE: Description
        """
        return self._parameters

    @parameters.setter
    def parameters(self, val: OrderedDict) -> None:
        """Summary.

        Returns:
            TYPE: Description
        """
        self._parameters = val

    @property
    def namespace(self) -> Optional[str]:
        """Summary.

        Returns:
            TYPE: Description
        """
        return self._namespace

    @property
    def subtopic(self) -> SubTopic:
        """Summary.

        Returns:
            TYPE: Description
        """
        return self._subtopic

    @property
    def pubtopic(self) -> PubTopic:
        """Summary.

        Returns:
            TYPE: Description
        """
        return self._pubtopic

    @pubtopic.setter
    def pubtopic(self, val: str) -> None:
        """Summary.

        Returns:
            TYPE: Description
        """
        self._pubtopic = val

    @property
    def func(self) -> str:
        """Summary.

        Returns:
            TYPE: Description
        """
        return self._func

    @property
    def host(self) -> str:
        """Summary.

        Returns:
            TYPE: Description
        """
        return self._host or ''

    @property
    def exchange(self) -> str:
        """Summary.

        Returns:
            TYPE: Description
        """
        return self._exchange or 'primary'

    @property
    def protocol(self) -> str:
        """Summary.

        Returns:
            TYPE: Description
        """
        return self._protocol

    @property
    def heartbeat(self) -> Optional[int]:
        """Summary.

        Returns:
            TYPE: Description
        """
        return int(self._heartbeat) if self._heartbeat else None

    def open_transaction(self):
        if not self._transaction_id:
            self._transaction_id = str(uuid.uuid4())
